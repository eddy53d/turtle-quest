-- 퀘스트·따라잡기 보너스. Supabase SQL Editor에서 한 번 실행.
-- 인증(activities)에서 계산되는 거리와 별개로, 보너스는 여기 쌓고 합산한다.
-- 관리자가 인증을 고쳐 거리를 다시 계산해도 보너스는 그대로 남는다.

create table if not exists bonuses (
  id         bigint generated always as identity primary key,
  user_id    uuid not null references users(id) on delete cascade,
  kind       text not null,          -- quest_memo | quest_cheer | quest_week5 | quest_full3 | catchup
  kst_date   date not null,          -- 일일은 그날, 주간은 그 주 월요일
  meters     int  not null,
  created_at timestamptz not null default now(),
  unique (user_id, kind, kst_date)   -- 같은 퀘스트는 하루(또는 한 주)에 한 번
);
create index if not exists bonuses_user_idx on bonuses (user_id);
alter table bonuses enable row level security;
