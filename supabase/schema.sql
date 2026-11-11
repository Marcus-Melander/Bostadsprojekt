-- Bostadsplan in Supabase: run in the project's SQL editor (safe to run again).
-- Anyone with the address sees the public part without signing in: the listings (without the agents'
-- descriptions), the finder's status and the price history. Signing in (only users you make: sign-ups are off)
-- gives each person their own plan; the owner also sees the private part: the travel times and morning digest (they show your important addresses
-- and saved apartments), the saved floor plans and the descriptions. The owner, whose plan the finder follows,
-- is filled in by `scraper/online.py --check` (the first user made). The Mac's uploads use the secret key,
-- which passes row-level security.

-- Signed in with a real user (not an anonymous sign-in)
create or replace function signed_in() returns boolean
  language sql stable
  as $$ select auth.uid() is not null and coalesce((auth.jwt() ->> 'is_anonymous')::boolean, false) = false $$;

create table if not exists owner (
  id int primary key default 1 check (id = 1),
  user_id uuid not null references auth.users on delete cascade
);
alter table owner enable row level security;
create or replace function is_owner() returns boolean
  language sql stable security definer set search_path = public
  as $$ select exists (select 1 from owner where user_id = auth.uid()) $$;
drop policy if exists "signed in see who owns it" on owner;
create policy "signed in see who owns it" on owner for select using (signed_in());

-- Each person's plan; the owner's also holds the finder's filter (data/plan.json and data/scrape.json on the Mac)
drop table if exists plan;  -- the first version's single shared plan, never used
create table if not exists plans (
  user_id uuid primary key references auth.users on delete cascade default auth.uid(),
  plan jsonb not null default '{}',
  scrape jsonb not null default '{}',
  updated_at timestamptz not null default now()
);
alter table plans enable row level security;
drop policy if exists "own plan" on plans;
create policy "own plan" on plans for all using (user_id = auth.uid()) with check (user_id = auth.uid());

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
drop policy if exists "signed in read apartments" on apartments;
drop policy if exists "signed in read readings" on readings;
drop policy if exists "anyone reads apartments" on apartments;
drop policy if exists "anyone reads readings" on readings;
create policy "anyone reads apartments" on apartments for select using (true);
create policy "anyone reads readings" on readings for select using (true);

-- Files: public/listings.json and public/status.json for anyone; private/ (the full listings, travel times,
-- digest, floorplans.json and floorplans/<file>) for the owner only
insert into storage.buckets (id, name, public) values ('data', 'data', false) on conflict (id) do nothing;
drop policy if exists "members read data files" on storage.objects;
drop policy if exists "signed in read shared files" on storage.objects;
drop policy if exists "owner reads private files" on storage.objects;
drop policy if exists "anyone reads public files" on storage.objects;
drop policy if exists "signed in read private files" on storage.objects;
create policy "anyone reads public files" on storage.objects for select
  using (bucket_id = 'data' and name like 'public/%');
create policy "signed in read private files" on storage.objects for select
  using (bucket_id = 'data' and name like 'private/%' and is_owner());

-- The first version's members list, replaced by signed_in()
drop table if exists members cascade;
drop function if exists is_member() cascade;
