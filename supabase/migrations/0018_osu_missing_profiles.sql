-- Additive and reversible without deleting participants or their historical stats.
alter table public.participants
  add column if not exists osu_profile_missing_at timestamptz;
comment on column public.participants.osu_profile_missing_at is
  'Profile endpoint returned HTTP 404; skip scheduled refresh until authenticated relink or manual reset.';
