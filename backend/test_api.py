"""pip install pytest pgserver httpx && pytest -q  (임베디드 Postgres로 schema.sql + API 전체 흐름 검증)"""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pgserver
import psycopg

pg = pgserver.get_server(tempfile.mkdtemp(), cleanup_mode="stop")
os.environ["DATABASE_URL"] = pg.get_uri()
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 32
with psycopg.connect(pg.get_uri(), autocommit=True) as c:
    c.execute(Path(__file__).with_name("schema.sql").read_text())
    PINS = dict(c.execute("select name, pin_code from users").fetchall())

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402


@pytest.fixture(scope="module")
def cl():
    """앱 시작/종료는 한 번만 — 커넥션 풀은 닫으면 다시 못 연다."""
    with TestClient(main.app) as client:
        yield client


def test_streak_rules():
    d = date(2026, 9, 22)
    assert main.next_streak(0, None, d) == 1
    assert main.next_streak(3, d - timedelta(1), d) == 4
    assert main.next_streak(3, d - timedelta(2), d) == 1  # 하루 빠지면 리셋
    assert main.next_streak(4, d, d) == 4
    assert main.effective_streak(4, d - timedelta(1), d) == 4  # 오늘 아직 안 했어도 유지
    assert main.effective_streak(4, d - timedelta(2), d) == 0  # 자정 지나 끊김
    assert [main.advance_for(1), main.advance_for(2)] == [5, 10]


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
    assert (r["advance"], r["total_distance"], r["streak"]) == (10, 15, 1)

    day = date(2026, 9, 22)  # 다음날: 이어서 완주하면 2일
    cl.post("/api/activity/check", json={"type": "exercise"}, headers=h)
    r = cl.post("/api/activity/check", json={"type": "qt"}, headers=h).json()
    assert (r["total_distance"], r["streak"], r["today"]) == (30, 2, {"qt": True, "exercise": True})
    assert cl.put("/api/activity/memo", json={"memo": "시편 23편"}, headers=h).status_code == 200

    board = cl.get("/api/race/leaderboard", headers=h).json()
    assert board[0]["name"] == "홍승원" and board[0]["memo"] == "시편 23편" and board[1]["name"] == "강서진"
    assert cl.get("/api/race/global-distance").json() == {"total_distance": 30}

    cal = cl.get(f"/api/activity/{me}/calendar?month=2026-09", headers=h).json()
    assert [c["date"] for c in cal] == ["2026-09-21", "2026-09-22"] and cal[0]["memo"] == "런닝 3km"

    day = date(2026, 9, 24)  # 하루 건너뜀 → 조회 시 0, 다음 완주 때 1부터
    assert cl.get("/api/race/leaderboard", headers=h).json()[0]["streak"] == 0

    other = board[1]["id"]
    assert cl.post("/api/social/poke", json={"to_user_id": me}, headers=h).status_code == 400
    assert cl.post("/api/social/poke", json={"to_user_id": other}, headers=h).status_code == 200
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
