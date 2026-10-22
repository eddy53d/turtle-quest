"""Turtle Quest API — FastAPI + Supabase(Postgres), Vercel 서버리스 함수로 배포.

프론트와 같은 도메인에서 /api/* 로 서빙된다(vercel.json의 rewrite).
env: DATABASE_URL, JWT_SECRET, RP_ID(도메인만), ORIGINS(패스키 검증용 origin), ADMIN_NAMES, DEFAULT_PIN
"""
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta
from datetime import time as clock_time  # stdlib time 모듈과 이름이 겹치지 않게
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

import jwt
import webauthn
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

KST = ZoneInfo("Asia/Seoul")
JWT_SECRET = os.environ["JWT_SECRET"]  # 필수: 32자 이상 랜덤 문자열
RP_ID = os.environ.get("RP_ID", "localhost")
ORIGINS = [o.strip() for o in os.environ.get("ORIGINS", "http://localhost:3000").split(",") if o.strip()]
ADMIN_NAMES = {n.strip() for n in os.environ.get("ADMIN_NAMES", "홍승원").split(",") if n.strip()}
DEFAULT_PIN = os.environ.get("DEFAULT_PIN", "0412")  # 공통 초기 PIN — 로그인하면 변경을 요구
BASE_STEP, SPURT_STEP = 5, 10           # 첫 인증 +5m, 같은 날 두 번째 인증 +10m(보너스 포함)

# 퀘스트 보상(m)과 조건. 보너스는 bonuses 테이블에 쌓이고 거리 재계산 때 합산된다.
QUEST_MEMO, QUEST_CHEER = 2, 3          # 일일: 한 줄 소감 / 소그룹 응원
QUEST_WEEK5, QUEST_FULL3 = 20, 15       # 주간: 5일 인증 / 완주 3일
CATCHUP_BONUS = 10                      # 평균에 한참 못 미치면 인증한 날마다 추가
CATCHUP_RATIO = 0.6                     # 소그룹 평균의 60% 미만이면 따라잡기 대상
WEEK_GOAL_TOTAL = 20                    # 협동 목표: 소그룹 전체 주 20회 인증

# ── 날짜/스트릭 로직 (KST, 순수 함수) ────────────────────────────────────────
# 자정 초기화는 크론 없이 처리: '오늘' 상태는 activities.kst_date로 매번 계산하고,
# 거리·연속일수는 recompute()가 전체 기록에서 다시 센다. 끊긴 스트릭은 조회 시 effective_streak가 0으로 보여준다.

def today_kst() -> date:
    return datetime.now(KST).date()


def effective_streak(streak: int, last: date | None, today: date) -> int:
    """어제 또는 오늘 완주했으면 유지, 아니면 끊긴 것(0)."""
    return streak if last and last >= today - timedelta(days=1) else 0


# ── DB ───────────────────────────────────────────────────────────────────────
# prepare_threshold=None: Supabase 트랜잭션 풀러(6543)는 prepared statement 미지원
# Vercel 서버리스 함수는 ASGI lifespan을 실행하지 않으므로 import 시점에 연다.
# 인스턴스가 여러 개 떠도 Supabase 트랜잭션 풀러가 받아내도록 인스턴스당 커넥션은 작게 잡는다.
pool = ConnectionPool(
    os.environ.get("DATABASE_URL", ""),
    kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": True},
    min_size=0, max_size=2, open=True, timeout=15,
)

app = FastAPI(title="Turtle Quest API")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


def q(sql: str, *args, one=False, params=None):
    with pool.connection() as c:
        cur = c.execute(sql, params if params is not None else args)
        rows = cur.fetchall() if cur.description else []
    return (rows[0] if rows else None) if one else rows


USER_SQL = "select u.*, exists(select 1 from passkeys p where p.user_id = u.id) has_passkey from users u"


# ── 인증 ─────────────────────────────────────────────────────────────────────
def make_token(user_id, ttl: timedelta = timedelta(days=30), **extra) -> str:
    return jwt.encode({"sub": str(user_id), "exp": datetime.now(KST) + ttl, **extra}, JWT_SECRET, "HS256")


def read_token(token: str, typ: str | None = None) -> dict:
    try:
        claims = jwt.decode(token, JWT_SECRET, ["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "다시 로그인해 주세요.")
    if claims.get("typ") != typ:
        raise HTTPException(401, "잘못된 토큰이에요.")
    return claims


def current_user(authorization: str = Header("")) -> str:
    return read_token(authorization.removeprefix("Bearer ").strip())["sub"]


def load_user(uid) -> dict:
    """토큰은 살아 있는데 DB에 사람이 없는 경우(계정 삭제 등)엔 다시 로그인시킨다."""
    u = q(f"{USER_SQL} where u.id = %s", uid, one=True)
    if not u:
        raise HTTPException(401, "다시 로그인해 주세요.")
    return u


def user_public(u: dict) -> dict:
    out = {"id": str(u["id"]), "name": u["name"], "sort_order": u["sort_order"],
           "has_passkey": bool(u.get("has_passkey")), "is_admin": u["name"] in ADMIN_NAMES}
    # 공개 목록에는 넣지 않음 — 누가 아직 공통 PIN을 쓰는지 드러나면 안 됨
    if "pin_code" in u:
        out["must_change_pin"] = u["pin_code"] == DEFAULT_PIN
    return out


# ponytail: 프로세스 메모리 카운터 — 인스턴스 1개 전제. 스케일아웃하면 DB 컬럼으로 옮길 것.
_pin_fails: dict[str, tuple[int, float]] = {}
MAX_FAILS, LOCK_SECONDS = 5, 300


class PinLogin(BaseModel):
    name: str
    pin: str = Field(pattern=r"^\d{4}$")


@app.get("/api/health")
def health():
    """외부 크론이 10분마다 호출해 Render 무료 플랜이 잠들지 않게 한다(콜드 스타트 30~60초 방지).
    DB까지 한 번 찔러서 커넥션 풀도 같이 깨워둔다."""
    q("select 1")
    return {"ok": True}


@app.get("/api/members")
def members():
    """로그인 전 이름 선택 화면용 (자주 인증하는 6명이 위)."""
    return [user_public(u) for u in q(
        "select u.id, u.name, u.sort_order, exists(select 1 from passkeys p where p.user_id = u.id) has_passkey "
        "from users u order by u.sort_order, u.name")]


@app.post("/api/auth/login-pin")
def login_pin(body: PinLogin):
    fails, since = _pin_fails.get(body.name, (0, 0.0))
    if fails >= MAX_FAILS and time.time() - since < LOCK_SECONDS:
        raise HTTPException(429, "PIN을 여러 번 틀렸어요. 5분 뒤에 다시 시도해 주세요.")
    u = q(f"{USER_SQL} where u.name = %s", body.name, one=True)
    if not u or not hmac.compare_digest(u["pin_code"], body.pin):
        _pin_fails[body.name] = (fails + 1 if fails < MAX_FAILS else 1, time.time())
        raise HTTPException(401, "이름 또는 PIN이 맞지 않아요.")
    _pin_fails.pop(body.name, None)
    return {"token": make_token(u["id"]), "user": user_public(u)}


class ChangePin(BaseModel):
    current_pin: str = Field(pattern=r"^\d{4}$")
    new_pin: str = Field(pattern=r"^\d{4}$")


@app.post("/api/auth/change-pin")
def change_pin(body: ChangePin, uid: str = Depends(current_user)):
    u = load_user(uid)
    if not hmac.compare_digest(u["pin_code"], body.current_pin):
        raise HTTPException(401, "현재 PIN이 맞지 않아요.")
    if body.new_pin == DEFAULT_PIN:
        raise HTTPException(400, "공통 PIN은 새 PIN으로 쓸 수 없어요.")
    if body.new_pin in {"0000", "1111", "1234", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999"}:
        raise HTTPException(400, "너무 쉬운 PIN이에요. 다른 번호로 정해 주세요.")
    q("update users set pin_code = %s where id = %s", body.new_pin, uid)
    return {"ok": True}


@app.get("/api/me")
def me(uid: str = Depends(current_user)):
    return user_public(load_user(uid))


# WebAuthn: challenge는 서버에 저장하지 않고 5분짜리 서명 토큰에 담아 왕복(무료 호스팅 재시작에도 안전).
def challenge_token(uid, challenge: bytes, typ: str) -> str:
    return make_token(uid, timedelta(minutes=5), typ=typ, chal=bytes_to_base64url(challenge))


class RegisterBody(BaseModel):
    credential: dict
    challenge_token: str


@app.post("/api/auth/webauthn/register-options")
def webauthn_register_options(uid: str = Depends(current_user)):
    u = load_user(uid)
    mine = q("select credential_id from passkeys where user_id = %s", uid)
    opts = webauthn.generate_registration_options(
        rp_id=RP_ID, rp_name="Turtle Quest", user_name=u["name"], user_id=str(u["id"]).encode(),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["credential_id"])) for p in mine],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED, user_verification=UserVerificationRequirement.PREFERRED),
    )
    return {"options": json.loads(webauthn.options_to_json(opts)), "challenge_token": challenge_token(uid, opts.challenge, "reg")}


@app.post("/api/auth/webauthn/register")
def webauthn_register(body: RegisterBody, uid: str = Depends(current_user)):
    claims = read_token(body.challenge_token, "reg")
    if claims["sub"] != uid:
        raise HTTPException(401, "잘못된 요청이에요.")
    try:
        v = webauthn.verify_registration_response(
            credential=body.credential, expected_challenge=base64url_to_bytes(claims["chal"]),
            expected_rp_id=RP_ID, expected_origin=ORIGINS)
    except Exception as e:
        raise HTTPException(400, f"패스키 등록 실패: {e}")
    q("insert into passkeys (credential_id, user_id, public_key, sign_count) values (%s, %s, %s, %s) "
      "on conflict (credential_id) do update set user_id = excluded.user_id, public_key = excluded.public_key, "
      "sign_count = excluded.sign_count",
      bytes_to_base64url(v.credential_id), uid, v.credential_public_key, v.sign_count)
    return {"ok": True}


class VerifyOptionsBody(BaseModel):
    name: str


class VerifyBody(BaseModel):
    credential: dict
    challenge_token: str


@app.post("/api/auth/webauthn/verify-options")
def webauthn_verify_options(body: VerifyOptionsBody):
    u = q("select id from users where name = %s", body.name, one=True)
    mine = q("select credential_id from passkeys where user_id = %s", u["id"]) if u else []
    if not mine:
        raise HTTPException(404, "등록된 지문·Face ID가 없어요. PIN으로 로그인해 주세요.")
    opts = webauthn.generate_authentication_options(
        rp_id=RP_ID,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(p["credential_id"])) for p in mine])
    return {"options": json.loads(webauthn.options_to_json(opts)), "challenge_token": challenge_token(u["id"], opts.challenge, "auth")}


@app.post("/api/auth/webauthn/verify")
def webauthn_verify(body: VerifyBody):
    claims = read_token(body.challenge_token, "auth")
    key = q("select * from passkeys where credential_id = %s and user_id = %s",
            body.credential.get("id"), claims["sub"], one=True)
    if not key:
        raise HTTPException(401, "이 기기의 지문·Face ID는 등록되어 있지 않아요.")
    try:
        v = webauthn.verify_authentication_response(
            credential=body.credential, expected_challenge=base64url_to_bytes(claims["chal"]),
            expected_rp_id=RP_ID, expected_origin=ORIGINS,
            credential_public_key=bytes(key["public_key"]), credential_current_sign_count=key["sign_count"])
    except Exception as e:
        raise HTTPException(401, f"생체 인증 실패: {e}")
    q("update passkeys set sign_count = %s, last_used_at = now() where credential_id = %s",
      v.new_sign_count, key["credential_id"])
    u = q(f"{USER_SQL} where u.id = %s", key["user_id"], one=True)
    return {"token": make_token(u["id"]), "user": user_public(u)}


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def quest_state(uid) -> dict:
    """오늘/이번 주 퀘스트 진행 상황. 달성한 것은 bonuses에 적립(중복 적립 없음)."""
    today, monday = today_kst(), monday_of(today_kst())
    todays = q("select type, memo from activities where user_id = %s and kst_date = %s", uid, today)
    cheers_today = q("select count(*) n from pokes where from_user = %s "
                     "and (created_at at time zone 'Asia/Seoul')::date = %s", uid, today, one=True)["n"]
    week = q("select kst_date, count(*) n from activities where user_id = %s and kst_date >= %s "
             "group by kst_date", uid, monday)
    group_week = q("select count(*) n from activities where kst_date >= %s", monday, one=True)["n"]

    done_types = {a["type"] for a in todays}
    quests = [
        {"id": "qt", "title": "큐티 인증", "reward": BASE_STEP, "done": "qt" in done_types, "period": "daily"},
        {"id": "exercise", "title": "운동 인증", "reward": BASE_STEP, "done": "exercise" in done_types, "period": "daily"},
        {"id": "both", "title": "둘 다 완료 (스퍼트)", "reward": SPURT_STEP, "done": len(done_types) >= 2, "period": "daily"},
        {"id": "quest_memo", "title": "한 줄 소감 남기기", "reward": QUEST_MEMO,
         "done": any(a["memo"] for a in todays), "period": "daily"},
        {"id": "quest_cheer", "title": "소그룹 친구 응원하기", "reward": QUEST_CHEER,
         "done": cheers_today > 0, "period": "daily"},
        {"id": "quest_week5", "title": "이번 주 5일 인증", "reward": QUEST_WEEK5,
         "done": len(week) >= 5, "progress": f"{len(week)}/5", "period": "weekly"},
        {"id": "quest_full3", "title": "이번 주 완주 3일", "reward": QUEST_FULL3,
         "done": sum(1 for d in week if d["n"] >= 2) >= 3,
         "progress": f"{sum(1 for d in week if d['n'] >= 2)}/3", "period": "weekly"},
    ]
    # 적립 대상은 추가 보상이 붙는 퀘스트뿐 (인증 자체는 이미 거리로 반영됨)
    payouts = {"quest_memo": (QUEST_MEMO, today), "quest_cheer": (QUEST_CHEER, today),
               "quest_week5": (QUEST_WEEK5, monday), "quest_full3": (QUEST_FULL3, monday)}
    earned = []
    for quest in quests:
        if quest["done"] and quest["id"] in payouts:
            meters, on = payouts[quest["id"]]
            if q("insert into bonuses (user_id, kind, kst_date, meters) values (%s, %s, %s, %s) "
                 "on conflict do nothing returning id", uid, quest["id"], on, meters, one=True):
                earned.append(quest["title"])
    return {"quests": quests, "earned": earned, "week_goal": {"done": group_week, "target": WEEK_GOAL_TOTAL}}


def grant_catchup(uid) -> int:
    """소그룹 평균에 한참 뒤처진 사람은 인증한 날마다 추가 거리를 받는다."""
    row = q("select s.total_distance mine, (select avg(total_distance) from race_stats) avg_ "
            "from race_stats s where s.user_id = %s", uid, one=True)
    if not row or row["mine"] >= float(row["avg_"] or 0) * CATCHUP_RATIO:
        return 0
    got = q("insert into bonuses (user_id, kind, kst_date, meters) values (%s, 'catchup', %s, %s) "
            "on conflict do nothing returning id", uid, today_kst(), CATCHUP_BONUS, one=True)
    return CATCHUP_BONUS if got else 0


@app.get("/api/quests")
def quests(uid: str = Depends(current_user)):
    state = quest_state(uid)
    if state["earned"]:
        recompute(uid)
    row = q("select s.total_distance mine, (select avg(total_distance) from race_stats) avg_ "
            "from race_stats s where s.user_id = %s", uid, one=True)
    behind = row and row["mine"] < float(row["avg_"] or 0) * CATCHUP_RATIO
    return {**state, "catchup": {"active": bool(behind), "bonus": CATCHUP_BONUS}}


# ── 활동 ─────────────────────────────────────────────────────────────────────
class CheckBody(BaseModel):
    type: Literal["qt", "exercise"]
    memo: str | None = Field(None, max_length=200)


@app.post("/api/activity/check")
def check(body: CheckBody, uid: str = Depends(current_user)):
    today = today_kst()
    before = q("select total_distance from race_stats where user_id = %s", uid, one=True)["total_distance"]
    if not q("insert into activities (user_id, type, memo, kst_date) values (%s, %s, %s, %s) "
             "on conflict do nothing returning id", uid, body.type, body.memo or None, today, one=True):
        raise HTTPException(409, "오늘은 이미 인증했어요.")
    # 인증 → 따라잡기·퀘스트 적립 → 거리 재계산(인증 + 보너스 합산) 순서
    catchup = grant_catchup(uid)
    earned = quest_state(uid)["earned"]
    s = recompute(uid)
    done = q("select type from activities where user_id = %s and kst_date = %s", uid, today)
    types = {d["type"] for d in done}
    return {"advance": s["total_distance"] - before, "total_distance": s["total_distance"],
            "streak": effective_streak(s["current_streak"], s["last_streak_date"], today),
            "catchup": catchup, "earned": earned,
            "today": {"qt": "qt" in types, "exercise": "exercise" in types}}


class MemoBody(BaseModel):
    memo: str = Field(max_length=200)


@app.put("/api/activity/memo")
def save_memo(body: MemoBody, uid: str = Depends(current_user)):
    """오늘 가장 최근 인증에 한 줄 소감 저장."""
    row = q("update activities set memo = %s where id = (select id from activities where user_id = %s "
            "and kst_date = %s order by created_at desc limit 1) returning id", body.memo or None, uid, today_kst(), one=True)
    if not row:
        raise HTTPException(400, "오늘 인증을 먼저 해 주세요.")
    earned = quest_state(uid)["earned"]
    if earned:
        recompute(uid)
    return {"ok": True, "earned": earned}


@app.get("/api/activity/{user_id}/calendar")
def calendar(user_id: UUID, month: str | None = None, _: str = Depends(current_user)):
    """month=YYYY-MM (기본: 이번 달). [{date, qt, exercise, memo}]"""
    first = datetime.strptime(month, "%Y-%m").date() if month else today_kst().replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    rows = q("select kst_date, bool_or(type = 'qt') qt, bool_or(type = 'exercise') exercise, "
             "string_agg(memo, ' / ') memo from activities where user_id = %s and kst_date >= %s and kst_date < %s "
             "group by kst_date order by kst_date", user_id, first, nxt)
    return [{"date": r["kst_date"].isoformat(), "qt": r["qt"], "exercise": r["exercise"], "memo": r["memo"]} for r in rows]


# ── 레이스 & 소셜 ────────────────────────────────────────────────────────────
@app.get("/api/race/leaderboard")
def leaderboard(_: str = Depends(current_user)):
    today = today_kst()
    rows = q("""
        select u.id, u.name, u.sort_order, u.turtle_items, s.total_distance, s.current_streak, s.last_streak_date,
               coalesce(bool_or(a.type = 'qt'), false) qt, coalesce(bool_or(a.type = 'exercise'), false) exercise,
               string_agg(a.memo, ' / ') memo,
               (select count(*) from pokes p where p.to_user = u.id) cheers,
               exists(select 1 from pokes p where p.from_user = u.id
                      and (p.created_at at time zone 'Asia/Seoul')::date = %(today)s) cheered
        from users u join race_stats s on s.user_id = u.id
        left join activities a on a.user_id = u.id and a.kst_date = %(today)s
        group by u.id, s.user_id
        order by s.total_distance desc, s.last_update asc, u.sort_order""", params={"today": today})
    return [{"rank": i + 1, "id": str(r["id"]), "name": r["name"], "sort_order": r["sort_order"],
             "turtle_items": r["turtle_items"], "total_distance": r["total_distance"],
             "streak": effective_streak(r["current_streak"], r["last_streak_date"], today),
             "today": {"qt": r["qt"], "exercise": r["exercise"]}, "memo": r["memo"] or "", "cheers": r["cheers"],
             # 오늘 일일 퀘스트를 몇 개 끝냈는지 (프론트에서 불붙은 거북이 판단용)
             "quests_done": sum([r["qt"], r["exercise"], bool(r["memo"]), r["cheered"]])}
            for i, r in enumerate(rows)]


@app.get("/api/race/global-distance")
def global_distance():
    return {"total_distance": q("select coalesce(sum(total_distance), 0) t from race_stats", one=True)["t"]}


class PokeBody(BaseModel):
    to_user_id: UUID


@app.post("/api/social/poke")
def poke(body: PokeBody, uid: str = Depends(current_user)):
    if str(body.to_user_id) == uid:
        raise HTTPException(400, "나에게는 응원을 보낼 수 없어요.")
    if not q("select 1 from users where id = %s", body.to_user_id, one=True):
        raise HTTPException(404, "없는 멤버예요.")
    q("insert into pokes (from_user, to_user) values (%s, %s)", uid, body.to_user_id)
    earned = quest_state(uid)["earned"]
    if earned:
        recompute(uid)
    return {"ok": True, "earned": earned}


@app.get("/api/social/pokes")
def my_pokes(uid: str = Depends(current_user)):
    """최근 24시간 동안 받은 응원 (프론트가 폴링해서 팝업 표시)."""
    rows = q("select p.id, u.name from_name, p.created_at from pokes p join users u on u.id = p.from_user "
             "where p.to_user = %s and p.created_at > now() - interval '24 hours' order by p.created_at desc", uid)
    return [{"id": r["id"], "from_name": r["from_name"], "created_at": r["created_at"].isoformat()} for r in rows]


# ── 관리자 (기본: 홍승원, 환경변수 ADMIN_NAMES로 변경) ───────────────────────
def current_admin(uid: str = Depends(current_user)) -> dict:
    u = load_user(uid)
    if u["name"] not in ADMIN_NAMES:
        raise HTTPException(403, "관리자만 쓸 수 있어요.")
    return u


def recompute(user_id) -> dict:
    """손으로 고친 기록으로 거리·연속일수를 다시 계산한다.
    하루 1개 5m, 2개면 보너스 포함 15m. 연속일수는 큐티+운동 둘 다 한 날만 센다."""
    return q("""
        with per_day as (
          select kst_date, count(*) n, max(created_at) at_ from activities where user_id = %(u)s group by kst_date
        ),
        dist as (select sum(case when n >= 2 then %(both)s else %(one)s end) total, max(at_) last_at from per_day),
        bonus as (select coalesce(sum(meters), 0) total from bonuses where user_id = %(u)s),
        full_days as (
          select kst_date, kst_date - (row_number() over (order by kst_date))::int * interval '1 day' grp
          from per_day where n >= 2
        ),
        runs as (
          select max(kst_date) last_day, count(*) len, row_number() over (order by max(kst_date) desc) rn
          from full_days group by grp
        )
        update race_stats s set
          total_distance = coalesce((select total from dist), 0) + (select total from bonus),
          current_streak = coalesce((select len from runs where rn = 1), 0),
          last_streak_date = (select last_day from runs where rn = 1),
          last_update = coalesce((select last_at from dist), now())
        where s.user_id = %(u)s returning *""", one=True,
             params={"u": user_id, "one": BASE_STEP, "both": BASE_STEP + SPURT_STEP})


def admin_rows(day: date) -> list[dict]:
    rows = q("""
        select u.id, u.name, s.total_distance, s.current_streak, s.last_streak_date,
               coalesce(bool_or(a.type = 'qt'), false) qt,
               coalesce(bool_or(a.type = 'exercise'), false) exercise,
               (select count(*) from passkeys p where p.user_id = u.id) devices,
               u.pin_code = %s as default_pin
        from users u join race_stats s on s.user_id = u.id
        left join activities a on a.user_id = u.id and a.kst_date = %s
        group by u.id, s.user_id order by u.sort_order, u.name""", DEFAULT_PIN, day)
    today = today_kst()
    return [{"id": str(r["id"]), "name": r["name"], "total_distance": r["total_distance"],
             "streak": effective_streak(r["current_streak"], r["last_streak_date"], today),
             "qt": r["qt"], "exercise": r["exercise"], "devices": r["devices"],
             "default_pin": r["default_pin"]} for r in rows]


@app.get("/api/admin/day")
def admin_day(day: str | None = None, _: dict = Depends(current_admin)):
    """특정 날짜(기본 오늘)의 14명 인증 현황."""
    d = datetime.strptime(day, "%Y-%m-%d").date() if day else today_kst()
    return {"date": d.isoformat(), "members": admin_rows(d)}


class AdminActivity(BaseModel):
    user_id: UUID
    day: date
    type: Literal["qt", "exercise"]
    done: bool


@app.post("/api/admin/activity")
def admin_set_activity(body: AdminActivity, _: dict = Depends(current_admin)):
    """인증을 대신 체크하거나 취소한다."""
    if body.day > today_kst():
        raise HTTPException(400, "아직 오지 않은 날짜예요.")
    if body.done:
        q("insert into activities (user_id, type, kst_date, created_at) values (%s, %s, %s, %s) "
          "on conflict (user_id, type, kst_date) do nothing",
          body.user_id, body.type, body.day, datetime.combine(body.day, clock_time(21, 0), KST))
    else:
        q("delete from activities where user_id = %s and type = %s and kst_date = %s",
          body.user_id, body.type, body.day)
    recompute(body.user_id)
    return {"date": body.day.isoformat(), "members": admin_rows(body.day)}


class AdminTarget(BaseModel):
    user_id: UUID


@app.post("/api/admin/reset-pin")
def admin_reset_pin(body: AdminTarget, _: dict = Depends(current_admin)):
    """PIN을 잊은 멤버를 공통 PIN으로 되돌린다. 다음 로그인 때 변경을 요구받는다."""
    q("update users set pin_code = %s where id = %s", DEFAULT_PIN, body.user_id)
    return {"ok": True, "pin": DEFAULT_PIN}


@app.post("/api/admin/reset-passkeys")
def admin_reset_passkeys(body: AdminTarget, _: dict = Depends(current_admin)):
    """기기를 잃어버렸을 때 등록된 지문·Face ID를 모두 해제한다."""
    q("delete from passkeys where user_id = %s", body.user_id)
    return {"ok": True}
