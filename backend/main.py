"""Turtle Quest API — FastAPI + Supabase(Postgres).

env: DATABASE_URL, JWT_SECRET, RP_ID(프론트 도메인, 예: turtle-quest.vercel.app), ORIGINS(쉼표 구분 프론트 origin)
"""
import hmac
import json
import os
import time
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
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
DEFAULT_PIN = os.environ.get("DEFAULT_PIN", "0412")  # 공통 초기 PIN — 로그인하면 변경을 요구
BASE_STEP, SPURT_STEP = 5, 10  # 첫 인증 +5m, 같은 날 두 번째 인증 +10m(보너스 5m 포함) — shared/quest.ts와 동일

# ── 날짜/스트릭 로직 (KST, 순수 함수) ────────────────────────────────────────
# 자정 초기화는 크론 없이 처리: '오늘' 상태는 activities.kst_date로 매번 계산하고,
# 끊긴 스트릭은 조회 시 effective_streak가 0으로 보여준다. 다음 완주 때 next_streak가 1로 재시작.

def today_kst() -> date:
    return datetime.now(KST).date()


def advance_for(done_today: int) -> int:
    """오늘 몇 번째 인증인지(1 또는 2)로 전진 거리 결정."""
    return SPURT_STEP if done_today >= 2 else BASE_STEP


def next_streak(streak: int, last: date | None, today: date) -> int:
    """오늘 두 개를 다 끝냈을 때의 새 스트릭."""
    if last == today:
        return streak
    return streak + 1 if last == today - timedelta(days=1) else 1


def effective_streak(streak: int, last: date | None, today: date) -> int:
    """어제 또는 오늘 완주했으면 유지, 아니면 끊긴 것(0)."""
    return streak if last and last >= today - timedelta(days=1) else 0


# ── DB ───────────────────────────────────────────────────────────────────────
# prepare_threshold=None: Supabase 트랜잭션 풀러(6543)는 prepared statement 미지원
pool = ConnectionPool(
    os.environ.get("DATABASE_URL", ""),
    kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": True},
    min_size=1, max_size=5, open=False,
)

@asynccontextmanager
async def lifespan(_):
    pool.open()
    yield
    pool.close()


app = FastAPI(title="Turtle Quest API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["*"], allow_headers=["*"])


def q(sql: str, *args, one=False):
    with pool.connection() as c:
        cur = c.execute(sql, args)
        rows = cur.fetchall() if cur.description else []
    return (rows[0] if rows else None) if one else rows


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


def user_public(u: dict) -> dict:
    out = {"id": str(u["id"]), "name": u["name"], "sort_order": u["sort_order"], "has_passkey": bool(u["credential_id"])}
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


@app.get("/api/members")
def members():
    """로그인 전 이름 선택 화면용 (자주 인증하는 6명이 위)."""
    return [user_public(u) for u in q("select id, name, sort_order, credential_id from users order by sort_order, name")]


@app.post("/api/auth/login-pin")
def login_pin(body: PinLogin):
    fails, since = _pin_fails.get(body.name, (0, 0.0))
    if fails >= MAX_FAILS and time.time() - since < LOCK_SECONDS:
        raise HTTPException(429, "PIN을 여러 번 틀렸어요. 5분 뒤에 다시 시도해 주세요.")
    u = q("select * from users where name = %s", body.name, one=True)
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
    u = q("select * from users where id = %s", uid, one=True)
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
    return user_public(q("select * from users where id = %s", uid, one=True))


# WebAuthn: challenge는 서버에 저장하지 않고 5분짜리 서명 토큰에 담아 왕복(무료 호스팅 재시작에도 안전).
def challenge_token(uid, challenge: bytes, typ: str) -> str:
    return make_token(uid, timedelta(minutes=5), typ=typ, chal=bytes_to_base64url(challenge))


class RegisterBody(BaseModel):
    credential: dict
    challenge_token: str


@app.post("/api/auth/webauthn/register-options")
def webauthn_register_options(uid: str = Depends(current_user)):
    u = q("select * from users where id = %s", uid, one=True)
    opts = webauthn.generate_registration_options(
        rp_id=RP_ID, rp_name="Turtle Quest", user_name=u["name"], user_id=str(u["id"]).encode(),
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
    q("update users set credential_id = %s, public_key = %s, sign_count = %s where id = %s",
      bytes_to_base64url(v.credential_id), v.credential_public_key, v.sign_count, uid)
    return {"ok": True}


class VerifyOptionsBody(BaseModel):
    name: str


class VerifyBody(BaseModel):
    credential: dict
    challenge_token: str


@app.post("/api/auth/webauthn/verify-options")
def webauthn_verify_options(body: VerifyOptionsBody):
    u = q("select * from users where name = %s and credential_id is not null", body.name, one=True)
    if not u:
        raise HTTPException(404, "이 기기에 등록된 패스키가 없어요. PIN으로 로그인해 주세요.")
    opts = webauthn.generate_authentication_options(
        rp_id=RP_ID, allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(u["credential_id"]))])
    return {"options": json.loads(webauthn.options_to_json(opts)), "challenge_token": challenge_token(u["id"], opts.challenge, "auth")}


@app.post("/api/auth/webauthn/verify")
def webauthn_verify(body: VerifyBody):
    claims = read_token(body.challenge_token, "auth")
    u = q("select * from users where id = %s", claims["sub"], one=True)
    if not u or u["credential_id"] != body.credential.get("id"):
        raise HTTPException(401, "등록된 패스키와 달라요.")
    try:
        v = webauthn.verify_authentication_response(
            credential=body.credential, expected_challenge=base64url_to_bytes(claims["chal"]),
            expected_rp_id=RP_ID, expected_origin=ORIGINS,
            credential_public_key=bytes(u["public_key"]), credential_current_sign_count=u["sign_count"])
    except Exception as e:
        raise HTTPException(401, f"생체 인증 실패: {e}")
    q("update users set sign_count = %s where id = %s", v.new_sign_count, u["id"])
    return {"token": make_token(u["id"]), "user": user_public(u)}


# ── 활동 ─────────────────────────────────────────────────────────────────────
class CheckBody(BaseModel):
    type: Literal["qt", "exercise"]
    memo: str | None = Field(None, max_length=200)


@app.post("/api/activity/check")
def check(body: CheckBody, uid: str = Depends(current_user)):
    today = today_kst()
    with pool.connection() as c, c.transaction():
        # race_stats 행을 먼저 잠가서 큐티/운동 동시 인증에도 보너스 계산이 꼬이지 않게 함
        s = c.execute("select * from race_stats where user_id = %s for update", (uid,)).fetchone()
        inserted = c.execute(
            "insert into activities (user_id, type, memo, kst_date) values (%s, %s, %s, %s) "
            "on conflict do nothing returning id", (uid, body.type, body.memo or None, today)).fetchone()
        if not inserted:
            raise HTTPException(409, "오늘은 이미 인증했어요.")
        done = c.execute("select type from activities where user_id = %s and kst_date = %s", (uid, today)).fetchall()
        advance = advance_for(len(done))
        streak, last = s["current_streak"], s["last_streak_date"]
        if len(done) >= 2:
            streak, last = next_streak(streak, last, today), today
        s = c.execute(
            "update race_stats set total_distance = total_distance + %s, current_streak = %s, "
            "last_streak_date = %s, last_update = now() where user_id = %s returning *",
            (advance, streak, last, uid)).fetchone()
    types = {d["type"] for d in done}
    return {"advance": advance, "total_distance": s["total_distance"],
            "streak": effective_streak(s["current_streak"], s["last_streak_date"], today),
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
    return {"ok": True}


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
               (select count(*) from pokes p where p.to_user = u.id) cheers
        from users u join race_stats s on s.user_id = u.id
        left join activities a on a.user_id = u.id and a.kst_date = %s
        group by u.id, s.user_id
        order by s.total_distance desc, s.last_update asc, u.sort_order""", today)
    return [{"rank": i + 1, "id": str(r["id"]), "name": r["name"], "sort_order": r["sort_order"],
             "turtle_items": r["turtle_items"], "total_distance": r["total_distance"],
             "streak": effective_streak(r["current_streak"], r["last_streak_date"], today),
             "today": {"qt": r["qt"], "exercise": r["exercise"]}, "memo": r["memo"] or "", "cheers": r["cheers"]}
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
    return {"ok": True}


@app.get("/api/social/pokes")
def my_pokes(uid: str = Depends(current_user)):
    """최근 24시간 동안 받은 응원 (프론트가 폴링해서 팝업 표시)."""
    rows = q("select p.id, u.name from_name, p.created_at from pokes p join users u on u.id = p.from_user "
             "where p.to_user = %s and p.created_at > now() - interval '24 hours' order by p.created_at desc", uid)
    return [{"id": r["id"], "from_name": r["from_name"], "created_at": r["created_at"].isoformat()} for r in rows]
