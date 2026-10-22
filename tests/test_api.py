"""python3 -m pip install -r requirements.txt pytest pgserver httpx && pytest -q

임베디드 Postgres에 db/schema.sql을 올리고 api/index.py의 API 흐름을 전부 돌려본다.
"""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pgserver
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))  # api/index.py 를 import
DB = Path(__file__).resolve().parents[1] / "db"

pg = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
os.environ["DATABASE_URL"] = pg.get_uri()
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 32
with psycopg.connect(pg.get_uri(), autocommit=True) as c:
    c.execute((DB / "schema.sql").read_text())
    PINS = dict(c.execute("select name, pin_code from users").fetchall())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import index as main  # noqa: E402


@pytest.fixture(scope="module")
def cl():
    """앱 시작/종료는 한 번만 — 커넥션 풀은 닫으면 다시 못 연다."""
    with TestClient(main.app) as client:
        yield client


def test_streak_rules():
    d = date(2026, 9, 22)
    assert main.effective_streak(4, d - timedelta(1), d) == 4  # 오늘 아직 안 했어도 유지
    assert main.effective_streak(4, d, d) == 4
    assert main.effective_streak(4, d - timedelta(2), d) == 0  # 자정 지나 끊김
    assert main.effective_streak(0, None, d) == 0
    assert main.monday_of(date(2026, 9, 25)) == date(2026, 9, 21)  # 금요일 → 그 주 월요일


def test_admin(monkeypatch, cl):
    monkeypatch.setattr(main, "today_kst", lambda: date(2026, 9, 25))
    admin = {"Authorization": "Bearer " + cl.post(
        "/api/auth/login-pin", json={"name": "홍승원", "pin": PINS["홍승원"]}).json()["token"]}
    member = {"Authorization": "Bearer " + cl.post(
        "/api/auth/login-pin", json={"name": "김지우", "pin": PINS["김지우"]}).json()["token"]}
    assert cl.get("/api/me", headers=admin).json()["is_admin"] is True
    assert cl.get("/api/me", headers=member).json()["is_admin"] is False
    assert cl.get("/api/admin/day", headers=member).status_code == 403   # 관리자만
    assert cl.get("/api/admin/day").status_code == 401

    jiwoo = next(m for m in cl.get("/api/admin/day", headers=admin).json()["members"] if m["name"] == "김지우")
    body = {"user_id": jiwoo["id"], "type": "qt", "done": True}
    assert cl.post("/api/admin/activity", json={**body, "day": "2026-09-26"}, headers=admin).status_code == 400

    def row(day, done, type_="qt"):
        r = cl.post("/api/admin/activity", json={"user_id": jiwoo["id"], "day": day, "type": type_, "done": done},
                    headers=admin).json()
        return next(m for m in r["members"] if m["name"] == "김지우")

    assert row("2026-09-24", True)["qt"] is True                      # 지난 날짜 대신 체크
    assert row("2026-09-25", True)["total_distance"] == 10            # 5m + 5m
    assert row("2026-09-25", True, "exercise")["total_distance"] == 20  # 오늘 완주 → 15m
    r = row("2026-09-25", True, "exercise")
    assert (r["streak"], r["devices"], r["default_pin"]) == (1, 0, False)
    assert row("2026-09-24", False)["total_distance"] == 15           # 취소하면 다시 빠진다
    assert row("2026-09-25", False, "exercise")["total_distance"] == 5

    assert cl.post("/api/admin/reset-pin", json={"user_id": jiwoo["id"]}, headers=admin).json()["pin"] == "0412"
    assert cl.post("/api/auth/login-pin", json={"name": "김지우", "pin": "0412"}).json()["user"]["must_change_pin"]
    assert cl.post("/api/admin/reset-passkeys", json={"user_id": jiwoo["id"]}, headers=admin).status_code == 200

    with psycopg.connect(pg.get_uri(), autocommit=True) as db:  # 다른 테스트를 위해 원상복구
        db.execute("delete from activities where user_id = %s", (jiwoo["id"],))
        db.execute("update users set pin_code = %s where id = %s", (PINS["김지우"], jiwoo["id"]))
    main.recompute(jiwoo["id"])


def test_quests_and_catchup(monkeypatch, cl):
    monkeypatch.setattr(main, "today_kst", lambda: date(2026, 10, 22))  # 목요일
    h = {"Authorization": "Bearer " + cl.post(
        "/api/auth/login-pin", json={"name": "박채원", "pin": PINS["박채원"]}).json()["token"]}
    me = cl.get("/api/me", headers=h).json()["id"]

    state = cl.get("/api/quests", headers=h).json()
    assert [q["id"] for q in state["quests"]] == [
        "qt", "exercise", "both", "quest_memo", "quest_cheer", "quest_week5", "quest_full3"]
    assert all(not q["done"] for q in state["quests"])
    assert state["week_goal"]["target"] == main.WEEK_GOAL_TOTAL
    assert state["catchup"]["active"] is False  # 아직 아무도 안 달렸으니 평균이 0

    # 앞서가는 사람이 생겨 평균이 오르면 뒤처진 사람은 따라잡기 대상이 된다
    with psycopg.connect(pg.get_uri(), autocommit=True) as db:
        lead = db.execute("select id from users where name = '장승재'").fetchone()[0]
        for d in range(1, 15):
            for kind in ("qt", "exercise"):
                db.execute("insert into activities (user_id, type, kst_date) values (%s, %s, %s)",
                           (lead, kind, date(2026, 10, d)))
    main.recompute(lead)
    assert cl.get("/api/quests", headers=h).json()["catchup"]["active"] is True

    r = cl.post("/api/activity/check", json={"type": "qt", "memo": "시편 1편"}, headers=h).json()
    assert r["catchup"] == main.CATCHUP_BONUS      # 평균에 한참 못 미치면 추가 지급
    assert r["advance"] == main.BASE_STEP + main.CATCHUP_BONUS + main.QUEST_MEMO
    assert r["earned"] == ["한 줄 소감 남기기"]
    assert cl.post("/api/activity/check", json={"type": "exercise"}, headers=h).json()["catchup"] == 0  # 하루 한 번

    # 응원 퀘스트 (created_at 기준이라 그날 시각으로 직접 넣는다)
    with psycopg.connect(pg.get_uri(), autocommit=True) as db:
        friend = db.execute("select id from users where name = '유수아'").fetchone()[0]
        db.execute("insert into pokes (from_user, to_user, created_at) values (%s, %s, %s)",
                   (me, friend, datetime(2026, 10, 22, 21, 0, tzinfo=main.KST)))
    assert "소그룹 친구 응원하기" in cl.get("/api/quests", headers=h).json()["earned"]

    after = cl.get("/api/quests", headers=h).json()
    assert [q["id"] for q in after["quests"] if q["done"]] == ["qt", "exercise", "both", "quest_memo", "quest_cheer"]
    assert next(q for q in after["quests"] if q["id"] == "quest_week5")["progress"] == "1/5"

    # 같은 주에 5일을 채우면 주간 퀘스트 20m
    with psycopg.connect(pg.get_uri(), autocommit=True) as db:
        for d in (19, 20, 21, 23):
            db.execute("insert into activities (user_id, type, kst_date) values (%s, 'qt', %s)", (me, date(2026, 10, d)))
    assert "이번 주 5일 인증" in cl.get("/api/quests", headers=h).json()["earned"]
    mine = next(m for m in cl.get("/api/race/leaderboard", headers=h).json() if m["id"] == me)
    assert mine["total_distance"] == (
        (main.BASE_STEP + main.SPURT_STEP) + 4 * main.BASE_STEP      # 완주한 하루 + 인증만 한 4일
        + main.CATCHUP_BONUS + main.QUEST_MEMO + main.QUEST_CHEER + main.QUEST_WEEK5)

    with psycopg.connect(pg.get_uri(), autocommit=True) as db:  # 다른 테스트를 위해 원상복구
        db.execute("delete from pokes where from_user = %s", (me,))
        db.execute("delete from activities where user_id in (%s, %s)", (me, lead))
        db.execute("delete from bonuses where user_id in (%s, %s)", (me, lead))
    main.recompute(me)
    main.recompute(lead)


def test_flow(monkeypatch, cl):
    names = [m["name"] for m in cl.get("/api/members").json()]
    assert names[:6] == ["홍승원", "강서진", "양시윤", "구준회", "조하은", "김유민"] and len(names) == 14

    bad = str((int(PINS["홍승원"]) + 1) % 10000).zfill(4)
    assert cl.post("/api/auth/login-pin", json={"name": "홍승원", "pin": bad}).status_code == 401
    r = cl.post("/api/auth/login-pin", json={"name": "홍승원", "pin": PINS["홍승원"]}).json()
    h = {"Authorization": f"Bearer {r['token']}"}
    me = r["user"]["id"]
    assert cl.get("/api/race/leaderboard").status_code == 401

    day = date(2026, 9, 21)
    monkeypatch.setattr(main, "today_kst", lambda: day)
    assert cl.post("/api/activity/check", json={"type": "qt"}, headers=h).json()["advance"] == 5
    assert cl.post("/api/activity/check", json={"type": "qt"}, headers=h).status_code == 409
    r = cl.post("/api/activity/check", json={"type": "exercise", "memo": "런닝 3km"}, headers=h).json()
    # 완주 보너스 + 한 줄 소감 퀘스트
    assert (r["advance"], r["total_distance"], r["streak"]) == (
        main.SPURT_STEP + main.QUEST_MEMO, main.BASE_STEP + main.SPURT_STEP + main.QUEST_MEMO, 1)
    assert r["earned"] == ["한 줄 소감 남기기"]

    day = date(2026, 9, 22)  # 다음날: 이어서 완주하면 2일
    cl.post("/api/activity/check", json={"type": "exercise"}, headers=h)
    r = cl.post("/api/activity/check", json={"type": "qt"}, headers=h).json()
    assert (r["total_distance"], r["streak"], r["today"]) == (32, 2, {"qt": True, "exercise": True})
    assert cl.put("/api/activity/memo", json={"memo": "시편 23편"}, headers=h).json()["earned"] == ["한 줄 소감 남기기"]

    board = cl.get("/api/race/leaderboard", headers=h).json()
    assert board[0]["name"] == "홍승원" and board[0]["memo"] == "시편 23편" and board[1]["name"] == "강서진"
    # 큐티+운동+소감은 했고 오늘 응원은 아직 → 4개 중 3개
    assert board[0]["quests_done"] == 3 and board[1]["quests_done"] == 0
    assert cl.get("/api/race/global-distance").json() == {"total_distance": 34}  # 소감 퀘스트 2m x 2일

    cal = cl.get(f"/api/activity/{me}/calendar?month=2026-09", headers=h).json()
    assert [c["date"] for c in cal] == ["2026-09-21", "2026-09-22"] and cal[0]["memo"] == "런닝 3km"

    day = date(2026, 9, 24)  # 하루 건너뜀 → 조회 시 0, 다음 완주 때 1부터
    assert cl.get("/api/race/leaderboard", headers=h).json()[0]["streak"] == 0

    other = board[1]["id"]
    assert cl.post("/api/social/poke", json={"to_user_id": me}, headers=h).status_code == 400
    poked = cl.post("/api/social/poke", json={"to_user_id": other}, headers=h)
    # 응원 퀘스트는 실제 시각 기준이라 today_kst를 과거로 바꾼 이 테스트에서는 달성되지 않는다
    assert poked.status_code == 200 and poked.json()["earned"] == []
    t2 = cl.post("/api/auth/login-pin", json={"name": "강서진", "pin": PINS["강서진"]}).json()["token"]
    assert cl.get("/api/social/pokes", headers={"Authorization": f"Bearer {t2}"}).json()[0]["from_name"] == "홍승원"

    # PIN 변경: 공통 PIN 사용자는 must_change_pin, 바꾸면 새 PIN으로만 로그인
    with psycopg.connect(pg.get_uri(), autocommit=True) as db:
        db.execute("update users set pin_code = '0412' where name = '양시윤'")
    s3 = cl.post("/api/auth/login-pin", json={"name": "양시윤", "pin": "0412"}).json()
    assert s3["user"]["must_change_pin"] is True
    h3 = {"Authorization": f"Bearer {s3['token']}"}
    assert cl.post("/api/auth/change-pin", json={"current_pin": "9999", "new_pin": "8274"}, headers=h3).status_code == 401
    assert cl.post("/api/auth/change-pin", json={"current_pin": "0412", "new_pin": "0412"}, headers=h3).status_code == 400
    assert cl.post("/api/auth/change-pin", json={"current_pin": "0412", "new_pin": "1234"}, headers=h3).status_code == 400
    assert cl.post("/api/auth/change-pin", json={"current_pin": "0412", "new_pin": "8274"}, headers=h3).status_code == 200
    assert cl.post("/api/auth/login-pin", json={"name": "양시윤", "pin": "0412"}).status_code == 401
    assert cl.post("/api/auth/login-pin", json={"name": "양시윤", "pin": "8274"}).json()["user"]["must_change_pin"] is False
    assert "must_change_pin" not in cl.get("/api/members").json()[0]  # 목록에는 노출 금지

    # 패스키: 기기 여러 개가 한 사람에게 붙고, 로그인 옵션에 모두 실린다
    assert cl.post("/api/auth/webauthn/verify-options", json={"name": "홍승원"}).status_code == 404
    o = cl.post("/api/auth/webauthn/register-options", headers=h).json()
    assert o["options"]["rp"]["id"] == "localhost" and o["challenge_token"]
    with psycopg.connect(pg.get_uri(), autocommit=True) as db:
        for cred in ("cGhvbmU", "dGFibGV0"):  # base64url("phone"), ("tablet")
            db.execute("insert into passkeys (credential_id, user_id, public_key) values (%s, %s, %s)",
                       (cred, me, b"fake"))
    allowed = {c["id"] for c in cl.post("/api/auth/webauthn/verify-options", json={"name": "홍승원"}).json()["options"]["allowCredentials"]}
    assert allowed == {"cGhvbmU", "dGFibGV0"}
    assert cl.get("/api/me", headers=h).json()["has_passkey"] is True
    # 이미 등록한 기기는 재등록 목록에서 제외
    o2 = cl.post("/api/auth/webauthn/register-options", headers=h).json()
    assert {c["id"] for c in o2["options"]["excludeCredentials"]} == {"cGhvbmU", "dGFibGV0"}
