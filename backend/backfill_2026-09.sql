-- 앱 만들기 전(9/19~9/23)에 카톡으로 인증한 기록 채워넣기. Supabase SQL Editor에서 한 번 실행.
-- 이미 앱으로 인증한 날은 건드리지 않고(ON CONFLICT DO NOTHING), 거리·연속일수는 전체 기록으로 다시 계산한다.

insert into activities (user_id, type, kst_date, created_at)
select u.id, v.type, v.d, v.d + time '21:00' at time zone 'Asia/Seoul'
from (values
  ('홍승원', 'qt',       '2026-09-19'::date),
  ('홍승원', 'exercise', '2026-09-19'::date),
  ('김지우', 'exercise', '2026-09-20'::date),
  ('강서진', 'qt',       '2026-09-21'::date),
  ('강서진', 'exercise', '2026-09-21'::date),
  ('구준회', 'qt',       '2026-09-21'::date),
  ('구준회', 'exercise', '2026-09-21'::date),
  ('양시윤', 'exercise', '2026-09-21'::date),
  ('김유민', 'qt',       '2026-09-21'::date),
  ('조하은', 'exercise', '2026-09-21'::date),
  ('조하은', 'qt',       '2026-09-22'::date),
  ('구준회', 'qt',       '2026-09-22'::date),
  ('홍승원', 'qt',       '2026-09-22'::date),
  ('강서진', 'qt',       '2026-09-22'::date),
  ('구준회', 'qt',       '2026-09-23'::date),
  ('강서진', 'qt',       '2026-09-23'::date),
  ('강서진', 'exercise', '2026-09-23'::date)
) as v(name, type, d)
join users u on u.name = v.name
on conflict (user_id, type, kst_date) do nothing;

-- 거리·연속일수 재계산 (하루 1개 5m, 2개면 보너스 포함 15m)
with per_day as (
  select user_id, kst_date, count(*) n, max(created_at) at_
  from activities group by user_id, kst_date
),
dist as (
  select user_id, sum(case when n >= 2 then 15 else 5 end) total, max(at_) last_at
  from per_day group by user_id
),
full_days as (   -- 큐티+운동 다 한 날만 모아 연속 구간(islands)을 찾는다
  select user_id, kst_date,
         kst_date - (row_number() over (partition by user_id order by kst_date))::int * interval '1 day' as grp
  from per_day where n >= 2
),
runs as (
  select user_id, max(kst_date) last_day, count(*) len,
         row_number() over (partition by user_id order by max(kst_date) desc) rn
  from full_days group by user_id, grp
)
insert into race_stats (user_id, total_distance, current_streak, last_streak_date, last_update)
select u.id, coalesce(d.total, 0), coalesce(r.len, 0), r.last_day, coalesce(d.last_at, now())
from users u
left join dist d on d.user_id = u.id
left join runs r on r.user_id = u.id and r.rn = 1
on conflict (user_id) do update set
  total_distance   = excluded.total_distance,
  current_streak   = excluded.current_streak,
  last_streak_date = excluded.last_streak_date,
  last_update      = excluded.last_update;

-- 결과 확인
select u.name, s.total_distance as 누적거리, s.current_streak as 연속일수, s.last_streak_date as 마지막완주일
from users u join race_stats s on s.user_id = u.id
order by s.total_distance desc, s.last_update, u.sort_order;
