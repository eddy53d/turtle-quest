-- Turtle Quest 스키마 + 14명 시드. Supabase SQL Editor에 통째로 붙여넣고 실행.
-- 다시 실행해도 안전(IF NOT EXISTS / ON CONFLICT). PIN은 최초 insert 때만 랜덤 생성.

create table if not exists users (
  id            uuid primary key default gen_random_uuid(),
  name          text not null unique,
  dob           date,                                  -- 관리자가 나중에 update
  pin_code      text not null check (pin_code ~ '^[0-9]{4}$'),
  credential_id text unique,                           -- WebAuthn credential id (base64url)
  public_key    bytea,                                 -- WebAuthn 공개키 (검증에 필수)
  sign_count    bigint not null default 0,
  turtle_items  jsonb  not null default '[]'::jsonb,
  sort_order    int    not null default 100            -- 목록 표기 순서 (낮을수록 위)
);

create table if not exists activities (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references users(id) on delete cascade,
  type       text not null check (type in ('qt', 'exercise')),
  memo       text check (char_length(memo) <= 200),
  created_at timestamptz not null default now(),
  kst_date   date not null default (now() at time zone 'Asia/Seoul')::date,
  unique (user_id, type, kst_date)                     -- 하루 종류별 1회 인증
);
create index if not exists activities_date_idx on activities (kst_date);

create table if not exists race_stats (
  user_id          uuid primary key references users(id) on delete cascade,
  total_distance   int  not null default 0,
  current_streak   int  not null default 0,
  last_streak_date date,                               -- 마지막으로 두 개 다 한 KST 날짜
  last_update      timestamptz not null default now()
);

create table if not exists pokes (
  id         bigint generated always as identity primary key,
  from_user  uuid not null references users(id) on delete cascade,
  to_user    uuid not null references users(id) on delete cascade,
  created_at timestamptz not null default now()
);
create index if not exists pokes_to_idx on pokes (to_user, created_at desc);

-- 백엔드만 DATABASE_URL(postgres 롤)로 접근. anon 키로는 못 읽게 RLS on + 정책 없음.
alter table users      enable row level security;
alter table activities enable row level security;
alter table race_stats enable row level security;
alter table pokes      enable row level security;

-- 14명 시드: 자주 인증하는 6명이 sort_order 1~6으로 상단.
insert into users (name, sort_order, pin_code)
select name, ord, lpad((floor(random() * 10000))::int::text, 4, '0')
from (values
  ('홍승원', 1), ('강서진', 2), ('양시윤', 3), ('구준회', 4), ('조하은', 5), ('김유민', 6),
  ('김지우', 7), ('박소연', 8), ('박채원', 9), ('유수아', 10), ('윤환희', 11), ('이수현', 12),
  ('장승재', 13), ('현예원', 14)
) as m(name, ord)
on conflict (name) do update set sort_order = excluded.sort_order;

insert into race_stats (user_id) select id from users on conflict do nothing;

-- 발급된 PIN 확인 (멤버들에게 개별 전달):
select sort_order, name, pin_code from users order by sort_order;
