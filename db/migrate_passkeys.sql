-- 기기별 패스키 여러 개 지원. Supabase SQL Editor에서 한 번 실행하세요.
-- 기존에 등록된 패스키는 그대로 옮겨오므로 다시 등록하지 않아도 됩니다.

create table if not exists passkeys (
  credential_id text primary key,
  user_id       uuid not null references users(id) on delete cascade,
  public_key    bytea not null,
  sign_count    bigint not null default 0,
  created_at    timestamptz not null default now(),
  last_used_at  timestamptz
);
create index if not exists passkeys_user_idx on passkeys (user_id);
alter table passkeys enable row level security;

insert into passkeys (credential_id, user_id, public_key, sign_count)
select credential_id, id, public_key, sign_count from users where credential_id is not null
on conflict (credential_id) do nothing;

-- 확인용: 누가 어떤 기기를 등록했는지
select u.name, count(p.credential_id) as 등록기기수
from users u left join passkeys p on p.user_id = u.id
group by u.name order by u.name;

-- (선택) 새 코드가 배포되어 잘 도는 걸 확인한 뒤에 실행하세요. 안 해도 동작에는 지장 없습니다.
-- alter table users drop column credential_id, drop column public_key, drop column sign_count;
