-- Bostadsplan in Supabase: run once in the project's SQL editor (safe to run again).
-- Everything is readable only by the users in `members`; the Mac's uploads use the secret key, which passes RLS.

create table if not exists members (
  user_id uuid primary key references auth.users on delete cascade
);
alter table members enable row level security;

create or replace function is_member() returns boolean
  language sql stable security definer set search_path = public
  as $$ select exists (select 1 from members where user_id = auth.uid()) $$;

drop policy if exists "members see themselves" on members;
create policy "members see themselves" on members for select using (user_id = auth.uid());

-- Your plan and the finder's filter (data/plan.json and data/scrape.json on the Mac)
create table if not exists plan (
  id int primary key default 1 check (id = 1),
  plan jsonb not null default '{}',
  scrape jsonb not null default '{}',
  updated_at timestamptz not null default now()
);
alter table plan enable row level security;
drop policy if exists "members read the plan" on plan;
drop policy if exists "members write the plan" on plan;
create policy "members read the plan" on plan for select using (is_member());
create policy "members write the plan" on plan for all using (is_member()) with check (is_member());

-- The price history (data/history.sqlite)
create table if not exists apartments (
  url text primary key, agent text, addr text, area text, district text, lat double precision, lon double precision,
  rooms double precision, kvm double precision, floor text, built int, avgift int, drift int, features jsonb,
  first_seen text, last_seen text, first_price bigint, last_price bigint,
  state text, highest_bid bigint, bids int, final_price bigint, sold_seen text, left_list text
);
create table if not exists readings (
  url text, day text, at text, price bigint, state text, bid bigint, bids int, final bigint,
  avgift int, next_showing text, showings int, on_list int,
  primary key (url, day)
);
alter table apartments enable row level security;
alter table readings enable row level security;
drop policy if exists "members read apartments" on apartments;
drop policy if exists "members read readings" on readings;
create policy "members read apartments" on apartments for select using (is_member());
create policy "members read readings" on readings for select using (is_member());

-- Files: listings.json, travel.json, status.json, digest.json, floorplans.json and floorplans/<file>
insert into storage.buckets (id, name, public) values ('data', 'data', false) on conflict (id) do nothing;
drop policy if exists "members read data files" on storage.objects;
create policy "members read data files" on storage.objects for select using (bucket_id = 'data' and is_member());
