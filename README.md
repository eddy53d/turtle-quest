# Turtle Quest — 다락방 2소그룹 큐티 & 운동 인증

Vercel 하나에 프론트와 API를 같이 올린다. DB만 Supabase.

```
api/index.py     FastAPI (Vercel 서버리스 함수, /api/* 로 서빙)
frontend/        React + Vite (정적 빌드)
db/*.sql         Supabase에서 실행하는 스크립트
tests/           pytest (임베디드 Postgres)
vercel.json      빌드·라우팅 설정
```

## 1. DB (Supabase)
- 처음이면 `db/schema.sql` 전체를 SQL Editor에 붙여넣고 실행 → 14명과 랜덤 PIN이 생성된다.
- 기존 DB에 기기별 패스키를 추가하려면 `db/migrate_passkeys.sql`.
- 과거 인증을 채워넣는 예시는 `db/backfill_2026-09.sql`(날짜·이름만 바꿔 재사용).

## 2. 배포 (Vercel)
프로젝트 설정 → Root Directory는 **저장소 루트(`.`)**. 빌드 명령·출력 경로·라우팅은 `vercel.json`이 정한다.

환경변수 (Production/Preview 모두):

| Key | 값 |
|---|---|
| `DATABASE_URL` | Supabase → Connect → **Transaction pooler** (포트 6543) |
| `JWT_SECRET` | 32자 이상 랜덤 (`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`) |
| `RP_ID` | 도메인만. 예: `turtle-quest-sigma.vercel.app` — 패스키가 이 도메인에 묶인다 |
| `ORIGINS` | `https://turtle-quest-sigma.vercel.app` — 패스키 검증에 쓰는 origin |
| `ADMIN_NAMES` | (선택) 관리자. 기본값 `홍승원` |
| `DEFAULT_PIN` | (선택) 공통 초기 PIN. 기본값 `0412` |

## 3. 로컬 실행
```bash
python3 -m pip install -r requirements.txt
uvicorn index:app --app-dir api --port 8000      # API
cd frontend && npx vite --port 3000              # /api 는 8000으로 프록시된다
pytest -q                                        # 테스트 (pytest pgserver httpx 필요)
```

## API
| | |
|---|---|
| `GET /api/health` | 상태 확인 |
| `GET /api/members` | 이름 선택용 목록 (자주 인증하는 6명 상단) |
| `POST /api/auth/login-pin` | `{name, pin}` → `{token, user}` (5회 실패 시 5분 잠금) |
| `POST /api/auth/change-pin` | `{current_pin, new_pin}` 본인 PIN 변경. 공통 PIN·쉬운 번호는 거부 |
| `POST /api/auth/webauthn/register-options` → `register` | 이 기기에 패스키 등록 (기기마다 한 줄) |
| `POST /api/auth/webauthn/verify-options` → `verify` | `{name}` → 생체 인증. 등록된 기기를 모두 허용 |
| `POST /api/activity/check` | `{type: qt\|exercise, memo?}` 첫 인증 +5m, 같은 날 두 번째 +10m(보너스 포함) |
| `PUT /api/activity/memo` | 오늘 인증에 한 줄 소감 |
| `GET /api/activity/{user_id}/calendar?month=YYYY-MM` | 잔디 |
| `GET /api/race/leaderboard` | 거리순(동점은 먼저 도달한 사람) + 오늘 상태/스트릭/소감/응원 수 |
| `GET /api/race/global-distance` | 14명 합계 |
| `POST /api/social/poke` / `GET /api/social/pokes` | 응원 보내기 / 최근 24시간 받은 응원 (프론트 30초 폴링) |
| `GET /api/admin/day?day=YYYY-MM-DD` | (관리자) 그 날짜의 14명 인증 현황 |
| `POST /api/admin/activity` | (관리자) `{user_id, day, type, done}` 대신 인증/취소 후 거리·연속일수 재계산 |
| `POST /api/admin/reset-pin` / `reset-passkeys` | (관리자) PIN을 공통 PIN으로, 등록 기기 전체 해제 |

스트릭: KST 기준 큐티+운동 둘 다 한 날이 연속된 일수. 자정 크론 없이 조회 시점에 계산 (어제·오늘 완주 없으면 0).

## 메모
- **Render에서 옮겨온 이유**: 무료 플랜은 월 750시간 제한이라 10분마다 깨우면 한 달을 못 버티고 서비스가 중지된다.
  Vercel 서버리스는 잠들지 않고 호출당 과금이라 이 문제가 없다. 깨우기용 외부 크론은 더 이상 필요 없다.
- 프론트와 API가 같은 도메인이라 CORS 설정이 필요 없다. `client/src/lib/api.ts`의 `BASE`는 비워 두는 게 기본값.
- Manus 개발용 런타임(367KB 인라인 스크립트)은 프로덕션 빌드에서 제외한다. 다시 넣으면 첫 화면이 그만큼 늦어진다.
- 프론트는 tRPC가 아니라 `client/src/lib/api.ts`의 fetch로 호출한다. main.tsx에 tRPC/react-query를 되살리지 말 것.
