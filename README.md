# Turtle Quest — 다락방 2소그룹 큐티 & 운동 인증

- `frontend/` React + Vite (Vercel)
- `backend/` FastAPI + Supabase Postgres (Render/Railway)

## 1. DB (Supabase)
SQL Editor에 `backend/schema.sql` 전체 붙여넣고 실행 → 마지막 결과가 14명의 랜덤 PIN 목록. 각자에게 개별 전달.
생년월일은 `update users set dob = '2000-01-01' where name = '홍승원';` 식으로 입력.

## 2. 백엔드 (Render)
- Root: `backend`, Build: `pip install -r requirements.txt`, Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- 환경변수
  - `DATABASE_URL` Supabase → Connect → Transaction pooler URI (6543)
  - `JWT_SECRET` 32자 이상 랜덤 (`python -c "import secrets;print(secrets.token_urlsafe(48))"`)
  - `RP_ID` 프론트 도메인만 (예: `turtle-quest.vercel.app`) — 패스키가 이 도메인에 묶임
  - `ORIGINS` 프론트 origin, 쉼표 구분 (예: `https://turtle-quest.vercel.app`)
  - `DEFAULT_PIN` (선택) 공통 초기 PIN, 기본값 `0412`. 이 PIN으로 로그인하면 앱이 변경 화면을 띄움

## 3. 프론트 (Vercel)
- Root: `frontend`, 환경변수 `VITE_API_URL` = 백엔드 URL

## API
| | |
|---|---|
| `GET /api/members` | 이름 선택용 목록 (자주 인증하는 6명 상단) |
| `POST /api/auth/login-pin` | `{name, pin}` → `{token, user}` (5회 실패 시 5분 잠금) |
| `POST /api/auth/change-pin` | `{current_pin, new_pin}` 본인 PIN 변경. 공통 PIN(`0412`)·쉬운 번호는 거부 |
| `POST /api/auth/webauthn/register-options` → `register` | PIN 로그인 후 패스키 등록 |
| `POST /api/auth/webauthn/verify-options` → `verify` | `{name}` → 생체 인증 → `{token, user}` |
| `POST /api/activity/check` | `{type: qt\|exercise, memo?}` 첫 인증 +5m, 같은 날 두 번째 +10m(보너스 포함) |
| `PUT /api/activity/memo` | 오늘 인증에 한 줄 소감 |
| `GET /api/activity/{user_id}/calendar?month=YYYY-MM` | 잔디 |
| `GET /api/race/leaderboard` | 거리순(동점은 먼저 도달한 사람) + 오늘 상태/스트릭/소감/응원 수 |
| `GET /api/race/global-distance` | 14명 합계 |
| `POST /api/social/poke` / `GET /api/social/pokes` | 응원 보내기 / 최근 24시간 받은 응원 (프론트 30초 폴링) |

스트릭: KST 기준 큐티+운동 둘 다 한 날이 연속된 일수. 자정 크론 없이 조회 시점에 계산 (어제·오늘 완주 없으면 0).

## 테스트
```
cd backend && pip install -r requirements.txt pytest pgserver httpx && pytest -q
```
