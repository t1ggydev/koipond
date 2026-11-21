-- Preparation only: apply to isolated staging before considering production.
-- Event payloads are transient work items, not a permanent audience chat log.
create schema if not exists koipond_internal;
revoke all on schema koipond_internal from public, anon, authenticated;

create table koipond_internal.channels (
  channel_id text primary key,
  lease_owner uuid,
  lease_until timestamptz
);
create table koipond_internal.events (
  id uuid primary key default gen_random_uuid(),
  seq bigint generated always as identity unique,
  message_id text not null unique,
  channel_id text not null references koipond_internal.channels(channel_id),
  payload jsonb,
  context jsonb,
  state text not null default 'pending' check (state in ('pending','processing','done','dead')),
  attempts integer not null default 0 check (attempts between 0 and 8),
  available_at timestamptz not null default now(),
  lease_owner uuid,
  lease_until timestamptz,
  received_at timestamptz not null default now(),
  finished_at timestamptz,
  error_code text
);
create index koi_events_head on koipond_internal.events(channel_id,seq)
  where state in ('pending','processing');
create index koi_events_retention on koipond_internal.events(finished_at)
  where state in ('done','dead');
create table koipond_internal.counters (
  singleton boolean primary key default true check (singleton),
  received bigint not null default 0,
  completed bigint not null default 0,
  retried bigint not null default 0,
  duplicate bigint not null default 0
);
insert into koipond_internal.counters(singleton) values(true);
create table koipond_internal.presence (
  subject_hash text primary key check (subject_hash ~ '^[0-9a-f]{64}$'),
  streamer boolean not null,
  seen_at timestamptz not null
);
create index koi_presence_expiry on koipond_internal.presence(seen_at);
create table koipond_internal.leases (
  name text primary key,
  owner uuid not null,
  expires_at timestamptz not null
);
create table koipond_internal.task_runs (
  name text primary key check (name='osu_stats'),
  last_success timestamptz,
  last_attempt timestamptz not null,
  failures bigint not null default 0
);
alter table koipond_internal.channels enable row level security;
alter table koipond_internal.events enable row level security;
alter table koipond_internal.counters enable row level security;
alter table koipond_internal.presence enable row level security;
alter table koipond_internal.leases enable row level security;
alter table koipond_internal.task_runs enable row level security;
revoke all on all tables in schema koipond_internal from public, anon, authenticated;
revoke all on all sequences in schema koipond_internal from public, anon, authenticated;

alter table public.draws add column source_event_id text;
create unique index koi_draws_event_id on public.draws(source_event_id) where source_event_id is not null;
alter table public.winner_messages add column source_event_id text;
create unique index koi_winner_messages_event_id on public.winner_messages(source_event_id) where source_event_id is not null;

-- A database revision, rather than a browser clock, fences all settings writers.
create function koipond_internal.bump_settings_revision()
returns trigger language plpgsql set search_path = pg_catalog,koipond_internal,public as $$
begin
  new.updated_at:=greatest(clock_timestamp(),old.updated_at+interval '1 microsecond');
  return new;
end $$;
create trigger koi_settings_revision before update on public.settings
  for each row execute function koipond_internal.bump_settings_revision();

create function public.koi_enqueue_event(p_message_id text,p_channel_id text,p_payload jsonb)
returns boolean language plpgsql security definer
set search_path = pg_catalog,koipond_internal,public as $$
declare v_id uuid; v_channel text; v_context jsonb;
begin
  if p_message_id is null or length(p_message_id) not between 1 and 256 or
     p_channel_id is null or length(p_channel_id) not between 1 and 128 or
     jsonb_typeof(p_payload) is distinct from 'object' or pg_column_size(p_payload)>1048576 then
    raise exception 'event_invalid' using errcode='22023';
  end if;
  delete from koipond_internal.events where id in (
    select e.id from koipond_internal.events e
    where e.finished_at < clock_timestamp()-interval '24 hours'
    order by e.finished_at limit 100
  );
  insert into koipond_internal.channels(channel_id) values(p_channel_id) on conflict do nothing;
  -- Assign sequence numbers only while holding the channel lock. Otherwise a
  -- later transaction could commit seq N+1 while seq N remains invisible.
  perform 1 from koipond_internal.channels c where c.channel_id=p_channel_id for update;
  -- Bind the event to the round which existed when it arrived. Looking up the
  -- round only after a backlog drains can otherwise move old messages into a
  -- newly opened giveaway. Never snapshot the secret number into queue context.
  select jsonb_build_object('source','ingress',
    'streamer',jsonb_build_object('id',st.id,'twitchId',st.twitch_id,
      'twitchLogin',st.twitch_login,'twitchDisplayName',st.twitch_display_name,
      'botScopeGranted',st.bot_scope_granted,'eventsubSubscriptionId',st.eventsub_subscription_id),
    'settingsRow',to_jsonb(s)-'number_target')
    into v_context from public.streamers st join public.settings s on s.streamer_id=st.id
    where st.twitch_id=p_channel_id;
  if not found then v_context:=jsonb_build_object('source','ingress','ignored',true); end if;
  insert into koipond_internal.events(message_id,channel_id,payload,context)
    values(p_message_id,p_channel_id,p_payload,v_context) on conflict(message_id) do nothing returning id into v_id;
  if v_id is null then
    select e.channel_id into v_channel from koipond_internal.events e where e.message_id=p_message_id;
    if v_channel is distinct from p_channel_id then
      raise exception 'event_channel_conflict' using errcode='22023';
    end if;
    update koipond_internal.counters set duplicate=duplicate+1 where singleton;
    return false;
  end if;
  update koipond_internal.counters set received=received+1 where singleton;
  return true;
end $$;

create function public.koi_claim_event(p_owner uuid,p_lease_seconds integer default 60)
returns table(id uuid,message_id text,channel_id text,payload jsonb,context jsonb,attempts integer,received_at timestamptz)
language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_channel text; v_job koipond_internal.events%rowtype; v_now timestamptz; v_until timestamptz;
begin
  if p_owner is null or p_lease_seconds is null or p_lease_seconds not between 10 and 300 then
    raise exception 'lease_invalid' using errcode='22023';
  end if;
  for i in 1..16 loop
    v_now:=clock_timestamp();
    select c.channel_id into v_channel from koipond_internal.channels c
    join lateral (select e.seq,e.available_at,e.lease_until from koipond_internal.events e
      where e.channel_id=c.channel_id and e.state in ('pending','processing') order by e.seq limit 1) h on true
    where (c.lease_until is null or c.lease_until<=v_now) and h.available_at<=v_now
      and (h.lease_until is null or h.lease_until<=v_now)
    order by h.seq for update of c skip locked limit 1;
    if not found then return; end if;
    select e.* into v_job from koipond_internal.events e where e.channel_id=v_channel
      and e.state in ('pending','processing') order by e.seq for update limit 1;
    if v_job.attempts>=8 then
      update koipond_internal.events e set state='dead',payload=null,context=null,
        finished_at=v_now,lease_owner=null,lease_until=null,error_code='retry_exhausted' where e.id=v_job.id;
      update koipond_internal.channels c set lease_owner=null,lease_until=null where c.channel_id=v_channel;
      continue;
    end if;
    v_until:=v_now+make_interval(secs=>p_lease_seconds);
    update koipond_internal.channels c set lease_owner=p_owner,lease_until=v_until where c.channel_id=v_channel;
    update koipond_internal.events e set state='processing',attempts=e.attempts+1,
      lease_owner=p_owner,lease_until=v_until where e.id=v_job.id returning e.* into v_job;
    if v_job.attempts>1 then update koipond_internal.counters set retried=retried+1 where singleton; end if;
    return query select v_job.id,v_job.message_id,v_job.channel_id,v_job.payload,v_job.context,v_job.attempts,v_job.received_at;
    return;
  end loop;
end $$;

create function public.koi_event_heartbeat(p_job_id uuid,p_owner uuid,p_lease_seconds integer default 60)
returns boolean language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_channel text; v_now timestamptz:=clock_timestamp(); v_until timestamptz;
begin
  if p_owner is null or p_lease_seconds is null or p_lease_seconds not between 10 and 300 then return false; end if;
  select e.channel_id into v_channel from koipond_internal.events e where e.id=p_job_id;
  if not found then return false; end if;
  perform 1 from koipond_internal.channels c where c.channel_id=v_channel and
    c.lease_owner=p_owner and c.lease_until>v_now for update;
  if not found then return false; end if;
  v_until:=v_now+make_interval(secs=>p_lease_seconds);
  update koipond_internal.events e set lease_until=v_until where e.id=p_job_id and
    e.state='processing' and e.lease_owner=p_owner and e.lease_until>v_now;
  if not found then return false; end if;
  update koipond_internal.channels c set lease_until=v_until where c.channel_id=v_channel;
  return true;
end $$;

create function public.koi_event_context(p_job_id uuid,p_owner uuid,p_context jsonb)
returns jsonb language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_channel text; v_result jsonb; v_now timestamptz:=clock_timestamp();
begin
  if jsonb_typeof(p_context) is distinct from 'object' or pg_column_size(p_context)>65536 then
    raise exception 'context_invalid' using errcode='22023';
  end if;
  select e.channel_id into v_channel from koipond_internal.events e where e.id=p_job_id;
  perform 1 from koipond_internal.channels c where c.channel_id=v_channel and
    c.lease_owner=p_owner and c.lease_until>v_now for update;
  if not found then raise exception 'lease_lost' using errcode='40001'; end if;
  update koipond_internal.events e set context=coalesce(e.context,p_context) where e.id=p_job_id
    and e.state='processing' and e.lease_owner=p_owner and e.lease_until>v_now returning e.context into v_result;
  if not found then raise exception 'lease_lost' using errcode='40001'; end if;
  return v_result;
end $$;

create function public.koi_finish_event(p_job_id uuid,p_owner uuid,p_success boolean,p_error_code text default null)
returns boolean language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_job koipond_internal.events%rowtype; v_channel text; v_now timestamptz:=clock_timestamp(); v_dead boolean;
begin
  if p_owner is null or p_success is null then return false; end if;
  select e.channel_id into v_channel from koipond_internal.events e where e.id=p_job_id;
  perform 1 from koipond_internal.channels c where c.channel_id=v_channel and
    c.lease_owner=p_owner and c.lease_until>v_now for update;
  if not found then return false; end if;
  select e.* into v_job from koipond_internal.events e where e.id=p_job_id and
    e.state='processing' and e.lease_owner=p_owner and e.lease_until>v_now for update;
  if not found then return false; end if;
  v_dead:=not p_success and v_job.attempts>=8;
  update koipond_internal.events e set
    state=case when p_success then 'done' when v_dead then 'dead' else 'pending' end,
    payload=case when p_success or v_dead then null else e.payload end,
    context=case when p_success or v_dead then null else e.context end,
    finished_at=case when p_success or v_dead then v_now else null end,
    available_at=case when p_success or v_dead then e.available_at else
      v_now+make_interval(secs=>least(60,power(2,least(v_job.attempts-1,6))::integer)) end,
    lease_owner=null,lease_until=null,
    error_code=case when p_success then null when v_dead then 'retry_exhausted'
      when p_error_code in ('processing_failed','lease_lost','timeout','upstream_failed','event_invalid','internal_error','shutdown')
        then p_error_code else 'processing_failed' end
    where e.id=p_job_id;
  update koipond_internal.channels c set lease_owner=null,lease_until=null where c.channel_id=v_channel;
  if p_success then update koipond_internal.counters set completed=completed+1 where singleton; end if;
  return true;
end $$;

create function public.koi_presence_touch(p_subject_hash text,p_streamer boolean)
returns void language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
begin
  if p_subject_hash is null or p_subject_hash !~ '^[0-9a-f]{64}$' or p_streamer is null then
    raise exception 'presence_invalid' using errcode='22023';
  end if;
  delete from koipond_internal.presence where subject_hash in (
    select p.subject_hash from koipond_internal.presence p
    where p.seen_at<clock_timestamp()-interval '1 hour' order by p.seen_at limit 100
  );
  insert into koipond_internal.presence(subject_hash,streamer,seen_at)
    values(p_subject_hash,p_streamer,clock_timestamp()) on conflict(subject_hash) do update
    set streamer=excluded.streamer,seen_at=excluded.seen_at
    where koipond_internal.presence.seen_at<clock_timestamp()-interval '30 seconds';
end $$;

create function public.koi_operational_metrics()
returns jsonb language sql security definer set search_path = pg_catalog,koipond_internal,public as $$
  select jsonb_build_object(
    'queue_pending',(select count(*) from koipond_internal.events where state='pending'),
    'queue_processing',(select count(*) from koipond_internal.events where state='processing'),
    'queue_dead',(select count(*) from koipond_internal.events where state='dead'),
    'queue_oldest_age_seconds',coalesce((select greatest(0,extract(epoch from clock_timestamp()-min(received_at)))
      from koipond_internal.events where state in ('pending','processing')),0),
    'events_received',c.received,'events_completed',c.completed,'events_retried',c.retried,'events_duplicate',c.duplicate,
    'active_users',(select count(*) from koipond_internal.presence where seen_at>clock_timestamp()-interval '5 minutes'),
    'active_streamers',(select count(*) from koipond_internal.presence where seen_at>clock_timestamp()-interval '5 minutes' and streamer),
    'stats_last_success_timestamp_seconds',coalesce((select extract(epoch from last_success) from koipond_internal.task_runs where name='osu_stats'),0),
    'stats_failures_total',coalesce((select failures from koipond_internal.task_runs where name='osu_stats'),0))
  from koipond_internal.counters c where c.singleton;
$$;

create function public.koi_acquire_lease(p_name text,p_owner uuid,p_seconds integer)
returns boolean language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
begin
  if p_name is null or p_name !~ '^[a-z0-9_.-]{1,64}$' or p_owner is null or p_seconds is null or p_seconds not between 1 and 900 then
    raise exception 'lease_invalid' using errcode='22023';
  end if;
  insert into koipond_internal.leases(name,owner,expires_at)
    values(p_name,p_owner,clock_timestamp()+make_interval(secs=>p_seconds)) on conflict(name) do update
    set owner=excluded.owner,expires_at=excluded.expires_at
    where koipond_internal.leases.expires_at<=clock_timestamp();
  return found;
end $$;

create function public.koi_record_task_run(p_name text,p_success boolean)
returns void language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_now timestamptz:=clock_timestamp();
begin
  if p_name is distinct from 'osu_stats' or p_success is null then
    raise exception 'task_invalid' using errcode='22023';
  end if;
  insert into koipond_internal.task_runs(name,last_attempt,last_success,failures)
    values(p_name,v_now,case when p_success then v_now else null end,case when p_success then 0 else 1 end)
    on conflict(name) do update set last_attempt=excluded.last_attempt,
      last_success=case when p_success then excluded.last_success else koipond_internal.task_runs.last_success end,
      failures=koipond_internal.task_runs.failures+case when p_success then 0 else 1 end;
end $$;
create function public.koi_release_lease(p_name text,p_owner uuid)
returns boolean language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
begin
  delete from koipond_internal.leases where name=p_name and owner=p_owner;
  return found;
end $$;

create function koipond_internal.draw_result(p_draw public.draws)
returns jsonb language sql immutable set search_path = pg_catalog,koipond_internal,public as $$
  select jsonb_build_object('draw_id',p_draw.id,'id',p_draw.id,'osu_username',p_draw.winner_osu_username,
    'winner_osu_username',p_draw.winner_osu_username,'hide_osu_stats',p_draw.hide_osu_stats,
    'winner_twitch_id',p_draw.winner_twitch_id,'winner_twitch_display_name',p_draw.winner_twitch_display_name,
    'winner_participant_id',p_draw.winner_participant_id,'created_at',p_draw.created_at,
    'source_event_id',p_draw.source_event_id,'session',p_draw.session);
$$;

create function public.koi_claim_number(p_streamer_id uuid,p_session integer,p_guess integer,p_message_id text,
  p_twitch_id text,p_twitch_display_name text,p_hide_osu_stats boolean,p_expected_number_drawn_at timestamptz default null)
returns jsonb language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_settings public.settings%rowtype; v_draw public.draws%rowtype; v_participant public.participants%rowtype;
begin
  if p_message_id is null or length(p_message_id) not between 1 and 256 or
     p_twitch_id is null or length(p_twitch_id) not between 1 and 128 then
    raise exception 'draw_invalid' using errcode='22023';
  end if;
  select s.* into v_settings from public.settings s where s.streamer_id=p_streamer_id for update;
  if not found then return null; end if;
  select d.* into v_draw from public.draws d where d.source_event_id=p_message_id;
  if found then
    if v_draw.streamer_id is distinct from p_streamer_id or v_draw.triggered_by<>'chat' then
      raise exception 'draw_channel_conflict' using errcode='22023'; end if;
    return koipond_internal.draw_result(v_draw);
  end if;
  if p_expected_number_drawn_at is not null and v_settings.number_drawn_at is distinct from p_expected_number_drawn_at then return null; end if;
  if v_settings.entries_session is distinct from p_session or not v_settings.entries_open or
     v_settings.draw_mode<>'number' or v_settings.number_target is distinct from p_guess then return null; end if;
  if v_settings.subscribers_only and not exists(select 1 from public.entries e where
    e.streamer_id=p_streamer_id and e.session=p_session and e.twitch_id=p_twitch_id and e.is_subscriber) then return null; end if;
  select p.* into v_participant from public.participants p where p.twitch_id=p_twitch_id;
  insert into public.draws(streamer_id,criteria,session,winner_participant_id,winner_twitch_id,
    winner_twitch_display_name,winner_osu_username,triggered_by,hide_osu_stats,source_event_id)
    values(p_streamer_id,'{}'::jsonb,p_session,v_participant.id,p_twitch_id,p_twitch_display_name,
      v_participant.osu_username,'chat',v_settings.hide_osu_stats or coalesce(p_hide_osu_stats,false),p_message_id)
    returning * into v_draw;
  update public.settings set entries_open=false,entries_session=entries_session+1,number_target=null,number_drawn_at=null,
    chat_capture_draw_id=v_draw.id,chat_capture_twitch_id=p_twitch_id,
    chat_capture_until=v_draw.created_at+interval '15 minutes',updated_at=clock_timestamp()
    where streamer_id=p_streamer_id;
  return koipond_internal.draw_result(v_draw);
end $$;

create function public.koi_commit_draw(p_streamer_id uuid,p_session integer,p_message_id text,p_winner jsonb,
  p_criteria jsonb,p_triggered_by text,p_hide_osu_stats boolean,p_expected_updated_at timestamptz default null)
returns jsonb language plpgsql security definer set search_path = pg_catalog,koipond_internal,public as $$
declare v_settings public.settings%rowtype; v_draw public.draws%rowtype; v_participant public.participants%rowtype;
  v_twitch_id text:=p_winner->>'twitchId'; v_participant_id uuid;
begin
  if p_message_id is null or length(p_message_id) not between 1 and 256 or
     v_twitch_id is null or length(v_twitch_id) not between 1 and 128 or
     p_triggered_by is null or p_triggered_by not in ('chat','dashboard') or jsonb_typeof(p_criteria) is distinct from 'object' then
    raise exception 'draw_invalid' using errcode='22023';
  end if;
  select s.* into v_settings from public.settings s where s.streamer_id=p_streamer_id for update;
  if not found then raise exception 'settings_missing' using errcode='22023'; end if;
  select d.* into v_draw from public.draws d where d.source_event_id=p_message_id;
  if found then
    if v_draw.streamer_id is distinct from p_streamer_id or v_draw.triggered_by is distinct from p_triggered_by then
      raise exception 'draw_channel_conflict' using errcode='22023'; end if;
    return koipond_internal.draw_result(v_draw);
  end if;
  if p_expected_updated_at is not null and v_settings.updated_at is distinct from p_expected_updated_at then
    raise exception 'settings_changed' using errcode='40001';
  end if;
  if v_settings.entries_session is distinct from p_session then raise exception 'session_changed' using errcode='40001'; end if;
  if v_settings.subscribers_only and not exists(select 1 from public.entries e where
    e.streamer_id=p_streamer_id and e.session=p_session and e.twitch_id=v_twitch_id and e.is_subscriber) then
    raise exception 'subscriber_required' using errcode='40001';
  end if;
  if v_settings.unique_winners and exists(select 1 from public.draws d where d.streamer_id=p_streamer_id
    and d.session=p_session and d.winner_twitch_id=v_twitch_id) then
    raise exception 'winner_already_drawn' using errcode='40001';
  end if;
  if coalesce((p_winner->>'linked')::boolean,false) then
    select p.* into v_participant from public.participants p where p.id=(p_winner->>'id')::uuid and p.twitch_id=v_twitch_id;
    if not found then raise exception 'winner_invalid' using errcode='22023'; end if;
    v_participant_id:=v_participant.id;
  end if;
  insert into public.draws(streamer_id,criteria,session,winner_participant_id,winner_twitch_id,
    winner_twitch_display_name,winner_osu_username,triggered_by,hide_osu_stats,source_event_id)
    values(p_streamer_id,p_criteria,p_session,v_participant_id,v_twitch_id,p_winner->>'twitchDisplayName',
      case when v_participant_id is not null then v_participant.osu_username else p_winner->>'osuUsername' end,
      p_triggered_by,v_settings.hide_osu_stats or coalesce(p_hide_osu_stats,false),p_message_id) returning * into v_draw;
  update public.settings set chat_capture_draw_id=v_draw.id,chat_capture_twitch_id=v_twitch_id,
    chat_capture_until=v_draw.created_at+interval '15 minutes',updated_at=clock_timestamp() where streamer_id=p_streamer_id;
  return koipond_internal.draw_result(v_draw);
end $$;

-- Migration 0012 gives new public routines broad default grants. Undo those
-- explicitly for every operational RPC; private schema objects stay inaccessible.
do $$ declare r record; begin
  for r in select p.oid::regprocedure as signature from pg_catalog.pg_proc p
    join pg_catalog.pg_namespace n on n.oid=p.pronamespace
    where n.nspname='public' and p.proname in ('koi_enqueue_event','koi_claim_event','koi_event_heartbeat',
      'koi_event_context','koi_finish_event','koi_presence_touch','koi_operational_metrics','koi_acquire_lease',
      'koi_release_lease','koi_record_task_run','koi_claim_number','koi_commit_draw') loop
    execute format('revoke all on function %s from public,anon,authenticated',r.signature);
    execute format('grant execute on function %s to service_role',r.signature);
  end loop;
end $$;
revoke all on all functions in schema koipond_internal from public,anon,authenticated;
notify pgrst, 'reload schema';
