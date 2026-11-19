#!/usr/bin/env python3
"""Concurrency and security acceptance checks in disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
import json
import os
from pathlib import Path
import select
import subprocess
import time
import uuid


def main():
    name = 'koipond-queue-check-' + uuid.uuid4().hex[:12]
    checks = 0
    created = False

    def command(argv, **kwargs):
        return subprocess.run(argv, capture_output=True, text=True, check=True, **kwargs)

    def sql(statement, check=True):
        result = subprocess.run(['docker', 'exec', '-i', name, 'psql', '-X', '-q', '-A', '-t',
                                 '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres'],
                                input=statement, capture_output=True, text=True, timeout=30)
        if check and result.returncode:
            raise AssertionError(result.stderr)
        return result

    def value(statement):
        text = sql(statement).stdout.strip()
        return json.loads(text) if text else None

    def expect(condition, description):
        nonlocal checks
        assert condition, description
        checks += 1

    def as_service(statement):
        return 'BEGIN; SET LOCAL ROLE service_role; ' + statement + '; COMMIT;'

    def enqueue(message, channel):
        return sql(as_service("SELECT public.koi_enqueue_event('%s','%s','{\"event\":\"synthetic\"}'::jsonb)" % (message, channel))).stdout.strip()

    def claim(owner):
        return value("SELECT coalesce(json_agg(row_to_json(j)),'[]'::json) FROM public.koi_claim_event('%s') j;" % owner)

    def finish(job, owner, success=True):
        return sql(as_service("SELECT public.koi_finish_event('%s','%s',%s,'processing_failed')" %
                              (job['id'], owner, 'true' if success else 'false'))).stdout.strip()

    def expire(job):
        sql("UPDATE koipond_internal.events SET lease_until=clock_timestamp()-interval '1 second',available_at=clock_timestamp()-interval '1 second' WHERE id='%s'; "
            "UPDATE koipond_internal.channels SET lease_until=clock_timestamp()-interval '1 second' WHERE channel_id='%s';" %
            (job['id'], job['channel_id']))

    try:
        command(['docker', 'run', '--rm', '-d', '--name', name, '--network', 'none', '--memory', '768m',
                 '--cpus', '1', '--tmpfs', '/var/lib/postgresql/data:rw,noexec,nosuid,size=256m',
                 '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgres:17-bookworm', '-c', 'wal_level=logical'], timeout=180)
        created = True
        for _ in range(90):
            process = command(['docker', 'exec', name, 'cat', '/proc/1/comm'])
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', 'postgres'], capture_output=True)
            if process.stdout.strip() == 'postgres' and ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated PostgreSQL startup failed')
        sql('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; CREATE PUBLICATION supabase_realtime;')
        migrations = sorted((Path(__file__).resolve().parent.parent / 'supabase/migrations').glob('*.sql'))
        assert len(migrations) >= 17
        for migration in migrations:
            sql('BEGIN;\n' + migration.read_text() + '\nCOMMIT;')

        ingress_streamer = str(uuid.uuid4())
        sql("INSERT INTO public.streamers(id,twitch_id,twitch_login,twitch_display_name) "
            "VALUES('%s','channel-a','channel_a','Channel A'); "
            "INSERT INTO public.settings(streamer_id,entries_session,entries_open,number_target) "
            "VALUES('%s',7,true,12345);" % (ingress_streamer, ingress_streamer))

        # A concurrent delivery of one message must create exactly one work item.
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda _: enqueue('same-delivery', 'channel-a'), range(12)))
        expect(outcomes.count('t') == 1 and outcomes.count('f') == 11, 'Atomic ingress deduplication')
        expect(value("SELECT count(*) FROM koipond_internal.events;") == 1, 'Only one durable event')
        expect(enqueue('a-second', 'channel-a') == 't', 'Second event queued')
        sql("UPDATE public.settings SET entries_session=8 WHERE streamer_id='%s';" % ingress_streamer)
        owners = [str(uuid.uuid4()) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(claim, owners))
        expect(sum(len(jobs) for jobs in claims) == 1, 'Only one worker owns a channel')
        index = next(i for i, jobs in enumerate(claims) if jobs)
        owner = owners[index]
        job = claims[index][0]
        expect(job['message_id'] == 'same-delivery', 'FIFO channel head claimed')
        expect(job['context']['source'] == 'ingress' and job['context']['settingsRow']['entries_session'] == 7,
               'Queued round N event keeps its receipt snapshot after round N+1 opens')
        expect('number_target' not in job['context']['settingsRow'] and 'number_drawn_at' in job['context']['settingsRow'],
               'Secret number excluded but generation timestamp retained')
        expect(bool(job['received_at']), 'Claim returns trusted database receipt time')
        expect(claim(str(uuid.uuid4())) == [], 'Later channel event cannot overtake active head')
        expect(enqueue('b-first', 'channel-b') == 't', 'Independent channel queued')
        other_owner = str(uuid.uuid4())
        other = claim(other_owner)[0]
        expect(other['channel_id'] == 'channel-b', 'Other channels can process concurrently')
        expect(other['context'] == {'source': 'ingress', 'ignored': True}, 'Unknown channel remains ignored at receipt')
        expect(finish(other, other_owner) == 't', 'Independent job completed')

        context = value("SELECT public.koi_event_context('%s','%s','{\"session\":7}'::jsonb);" % (job['id'], owner))
        replay_context = value("SELECT public.koi_event_context('%s','%s','{\"session\":99}'::jsonb);" % (job['id'], owner))
        expect(context == replay_context == job['context'], 'Ingress context is immutable across worker retries')
        expect(sql("SELECT public.koi_event_heartbeat('%s','%s');" % (job['id'], owner)).stdout.strip() == 't', 'Heartbeat renews current owner')
        expect(finish(job, owner, False) == 't', 'Retry scheduled after failure')
        expect(claim(str(uuid.uuid4())) == [], 'Backoff blocks head and later channel jobs')
        sql("UPDATE koipond_internal.events SET available_at=clock_timestamp()-interval '1 second' WHERE id='%s';" % job['id'])
        new_owner = str(uuid.uuid4())
        retried = claim(new_owner)[0]
        expect(retried['id'] == job['id'] and retried['attempts'] == 2 and retried['context'] == job['context'], 'Retry preserves event and ingress snapshot')
        expect(finish(job, owner) == 'f', 'Stale owner cannot finish a reassigned event')
        expect(sql("SELECT public.koi_event_heartbeat('%s','%s');" % (job['id'], owner)).stdout.strip() == 'f', 'Stale owner cannot heartbeat')
        expect(sql("SELECT public.koi_event_context('%s','%s','{}');" % (job['id'], owner), check=False).returncode != 0, 'Stale owner cannot modify context')
        expire(retried)
        expect(sql("SELECT public.koi_event_heartbeat('%s','%s');" % (job['id'], new_owner)).stdout.strip() == 'f', 'Expired owner cannot renew before replacement')
        expect(finish(job, new_owner) == 'f', 'Expired owner cannot finish before replacement')
        recovered_owner = str(uuid.uuid4())
        recovered = claim(recovered_owner)[0]
        expect(recovered['id'] == job['id'] and recovered['attempts'] == 3, 'Dead worker lease recovery')
        expect(finish(recovered, recovered_owner) == 't', 'Recovered job completed')
        scrubbed = value("SELECT json_build_object('payload',payload,'context',context,'state',state) FROM koipond_internal.events WHERE id='%s';" % job['id'])
        expect(scrubbed == {'payload': None, 'context': None, 'state': 'done'}, 'Payload removed on success')
        next_owner = str(uuid.uuid4())
        next_job = claim(next_owner)[0]
        expect(next_job['message_id'] == 'a-second', 'FIFO continues after successful head')
        sql("UPDATE koipond_internal.events SET attempts=8 WHERE id='%s';" % next_job['id'])
        expect(finish(next_job, next_owner, False) == 't', 'Retry limit handled')
        expect(value("SELECT json_build_object('state',state,'payload',payload,'context',context) FROM koipond_internal.events WHERE id='%s';" % next_job['id']) ==
               {'state': 'dead', 'payload': None, 'context': None}, 'Dead payload scrubbed')
        enqueue('crash-final', 'channel-final')
        final_owner = str(uuid.uuid4())
        final_job = claim(final_owner)[0]
        sql("UPDATE koipond_internal.events SET attempts=8 WHERE id='%s';" % final_job['id'])
        expire(final_job)
        expect(claim(str(uuid.uuid4())) == [], 'Expired final attempt is dead-lettered')
        expect(value("SELECT to_json(state) FROM koipond_internal.events WHERE id='%s';" % final_job['id']) == 'dead', 'No permanently stuck eighth attempt')

        denied = sql("BEGIN; SET LOCAL ROLE anon; SELECT public.koi_operational_metrics();", check=False)
        expect(denied.returncode != 0 and 'permission denied' in denied.stderr, 'Anonymous RPC execution denied')
        denied = sql("BEGIN; SET LOCAL ROLE authenticated; SELECT * FROM koipond_internal.events;", check=False)
        expect(denied.returncode != 0 and 'permission denied' in denied.stderr, 'Private payloads inaccessible')
        denied_count = value("SELECT count(*) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                             "WHERE n.nspname='public' AND p.proname LIKE 'koi_%' AND "
                             "(pg_catalog.has_function_privilege('anon',p.oid,'EXECUTE') OR pg_catalog.has_function_privilege('authenticated',p.oid,'EXECUTE'));")
        expect(denied_count == 0, 'All operational RPCs revoke inherited broad default grants')

        # Shared presence uses hashes, deduplicates tabs and expires without cleanup dependence.
        subject = 'a' * 64
        sql(as_service("SELECT public.koi_presence_touch('%s',true)" % subject))
        seen = sql("SELECT seen_at::text FROM koipond_internal.presence;").stdout.strip()
        sql(as_service("SELECT public.koi_presence_touch('%s',true)" % subject))
        expect(sql("SELECT seen_at::text FROM koipond_internal.presence;").stdout.strip() == seen, 'Presence writes rate-limited')
        metrics = value('SELECT public.koi_operational_metrics();')
        expect(metrics['active_users'] == metrics['active_streamers'] == 1, 'Shared presence counts user once')
        sql("UPDATE koipond_internal.presence SET seen_at=clock_timestamp()-interval '6 minutes';")
        metrics = value('SELECT public.koi_operational_metrics();')
        expect(metrics['active_users'] == metrics['active_streamers'] == 0, 'Presence expires at five minutes')

        lease_a, lease_b = str(uuid.uuid4()), str(uuid.uuid4())
        expect(sql(as_service("SELECT public.koi_acquire_lease('stats','%s',600)" % lease_a)).stdout.strip() == 't', 'Singleton lease acquired')
        expect(sql(as_service("SELECT public.koi_acquire_lease('stats','%s',600)" % lease_b)).stdout.strip() == 'f', 'Second scheduler blocked')
        expect(sql(as_service("SELECT public.koi_release_lease('stats','%s')" % lease_b)).stdout.strip() == 'f', 'Lease release fenced')
        sql("UPDATE koipond_internal.leases SET expires_at=clock_timestamp()-interval '1 second';")
        expect(sql(as_service("SELECT public.koi_acquire_lease('stats','%s',600)" % lease_b)).stdout.strip() == 't', 'Expired singleton lease recovered')
        expect(sql(as_service("SELECT public.koi_release_lease('stats','%s')" % lease_a)).stdout.strip() == 'f', 'Old scheduler cannot release replacement')
        sql(as_service("SELECT public.koi_record_task_run('osu_stats',false)"))
        metrics = value('SELECT public.koi_operational_metrics();')
        expect(metrics['stats_failures_total'] == 1 and metrics['stats_last_success_timestamp_seconds'] == 0,
               'Task failure increments durable count without claiming success')
        sql(as_service("SELECT public.koi_record_task_run('osu_stats',true)"))
        metrics = value('SELECT public.koi_operational_metrics();')
        expect(metrics['stats_failures_total'] == 1 and metrics['stats_last_success_timestamp_seconds'] > 0,
               'Task success records timestamp and preserves failure count')
        expect(sql(as_service("SELECT public.koi_record_task_run('unbounded-task-name',true)"), check=False).returncode != 0,
               'Task names restricted to the declared scheduler')

        streamer, streamer_other = str(uuid.uuid4()), str(uuid.uuid4())
        sql("INSERT INTO public.streamers(id,twitch_id,twitch_login,twitch_display_name) VALUES "
            "('%s','synthetic-channel','synthetic','Synthetic'),('%s','synthetic-other','other','Other'); "
            "INSERT INTO public.settings(streamer_id,entries_session,entries_open,draw_mode,number_target,unique_winners,number_drawn_at) "
            "VALUES('%s',7,true,'number',42,false,clock_timestamp()),('%s',1,true,'keyword',null,true,null);" %
            (streamer, streamer_other, streamer, streamer_other))
        number_revision = sql("SELECT number_drawn_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip()
        number_sql = "SELECT public.koi_claim_number('%s',7,42,'number-event','synthetic-winner','Synthetic Winner',false,'%s'::timestamptz);" % (streamer, number_revision)
        first_number = value(number_sql)
        expect(first_number and first_number['session'] == 7, 'Number claim records winner')
        expect(value(number_sql)['draw_id'] == first_number['draw_id'], 'Number replay returns same draw')
        state = value("SELECT json_build_object('session',entries_session,'open',entries_open,'target',number_target) FROM public.settings WHERE streamer_id='%s';" % streamer)
        expect(state == {'session': 8, 'open': False, 'target': None}, 'Number close and session advance atomic and idempotent')
        expect(value("SELECT public.koi_claim_number('%s',7,42,'different-number','another','Another',false);" % streamer) is None, 'A second winning guess cannot claim a closed round')
        expect(sql("SELECT public.koi_claim_number('%s',1,42,'number-event','x','X',false);" % streamer_other, check=False).returncode != 0, 'Draw replay cannot cross channels')
        sql("UPDATE public.settings SET entries_session=9,entries_open=true,number_target=17 WHERE streamer_id='%s';" % streamer)
        def number_race(event):
            return value("SELECT public.koi_claim_number('%s',9,17,'%s','synthetic-winner','Synthetic Winner',false);" % (streamer, event))
        with ThreadPoolExecutor(max_workers=2) as pool:
            number_results = list(pool.map(number_race, ['number-race-a', 'number-race-b']))
        expect(sum(result is not None for result in number_results) == 1, 'Concurrent correct guesses produce exactly one winner')
        expect(value("SELECT count(*) FROM public.draws WHERE streamer_id='%s' AND session=9;" % streamer) == 1, 'Number winner persisted once under concurrency')
        sql("UPDATE public.settings SET entries_session=11,entries_open=true,number_target=21,number_drawn_at=clock_timestamp() WHERE streamer_id='%s';" % streamer)
        stale_number_revision = sql("SELECT number_drawn_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip()
        old_settings_revision = sql("SELECT updated_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip()
        sql("UPDATE public.settings SET chat_announcement=not chat_announcement WHERE streamer_id='%s';" % streamer)
        expect(old_settings_revision != sql("SELECT updated_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip(),
               'Database revision still changes for unrelated settings updates')
        expect(value("SELECT public.koi_claim_number('%s',11,21,'harmless-autosave','fresh-guesser','Fresh Guesser',false,'%s'::timestamptz);" %
                     (streamer, stale_number_revision)) is not None, 'Unrelated settings autosave does not discard a correct guess')
        sql("UPDATE public.settings SET entries_session=13,entries_open=true,number_target=21,number_drawn_at=clock_timestamp() WHERE streamer_id='%s';" % streamer)
        stale_number_revision = sql("SELECT number_drawn_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip()
        sql("UPDATE public.settings SET number_target=21,number_drawn_at=clock_timestamp() WHERE streamer_id='%s';" % streamer)
        expect(value("SELECT public.koi_claim_number('%s',13,21,'stale-number','late-guesser','Late Guesser',false,'%s'::timestamptz);" %
                     (streamer, stale_number_revision)) is None, 'Number guess against a new number generation is discarded')
        current_number_revision = sql("SELECT number_drawn_at::text FROM public.settings WHERE streamer_id='%s';" % streamer).stdout.strip()
        expect(stale_number_revision != current_number_revision, 'A redraw changes the number generation')
        expect(value("SELECT public.koi_claim_number('%s',13,21,'fresh-number','fresh-guesser','Fresh Guesser',false,'%s'::timestamptz);" %
                     (streamer, current_number_revision)) is not None, 'Current revision can claim the number')

        winner = json.dumps({'id': 'twitch:synthetic-candidate', 'linked': False, 'twitchId': 'synthetic-candidate', 'twitchDisplayName': 'Candidate', 'osuUsername': None})
        def commit(event):
            return sql("SELECT public.koi_commit_draw('%s',1,'%s','%s'::jsonb,'{}'::jsonb,'chat',false);" %
                       (streamer_other, event, winner), check=False)
        with ThreadPoolExecutor(max_workers=2) as pool:
            draws = list(pool.map(commit, ['draw-a', 'draw-b']))
        expect(sum(r.returncode == 0 for r in draws) == 1, 'Concurrent unique-winner commits serialize')
        success_index = next(i for i, result in enumerate(draws) if result.returncode == 0)
        event = ['draw-a', 'draw-b'][success_index]
        original = json.loads(draws[success_index].stdout)
        expect(json.loads(commit(event).stdout)['draw_id'] == original['draw_id'], 'Chat command replay cannot redraw')
        expect(sql("SELECT public.koi_commit_draw('%s',1,'%s','%s','{}','dashboard',false);" %
                   (streamer_other, event, winner), check=False).returncode != 0, 'Replay cannot switch trigger type')
        stale_revision = sql("SELECT updated_at::text FROM public.settings WHERE streamer_id='%s';" % streamer_other).stdout.strip()
        sql("UPDATE public.settings SET subscribers_only=true WHERE streamer_id='%s';" % streamer_other)
        subscriber = json.dumps({'id': 'twitch:subscriber-candidate', 'linked': False, 'twitchId': 'subscriber-candidate',
                                 'twitchDisplayName': 'Subscriber Candidate', 'osuUsername': None})
        denied_subscriber = sql("SELECT public.koi_commit_draw('%s',1,'subscriber-event','%s','{}','chat',false);" %
                                (streamer_other, subscriber), check=False)
        expect(denied_subscriber.returncode != 0 and 'subscriber_required' in denied_subscriber.stderr,
               'A newly enabled subscriber filter blocks an already selected non-subscriber')
        sql("INSERT INTO public.entries(streamer_id,session,twitch_id,twitch_login,twitch_display_name,is_subscriber) "
            "VALUES('%s',1,'subscriber-candidate','subscriber','Subscriber Candidate',true);" % streamer_other)
        stale_commit = sql("SELECT public.koi_commit_draw('%s',1,'subscriber-event','%s','{}','chat',false,'%s'::timestamptz);" %
                           (streamer_other, subscriber, stale_revision), check=False)
        expect(stale_commit.returncode != 0 and 'settings_changed' in stale_commit.stderr,
               'A stale settings revision is rejected before committing a draw')
        current_revision = sql("SELECT updated_at::text FROM public.settings WHERE streamer_id='%s';" % streamer_other).stdout.strip()
        subscriber_sql = ("SELECT public.koi_commit_draw('%s',1,'subscriber-event','%s','{}','chat',false,'%s'::timestamptz);" %
                          (streamer_other, subscriber, current_revision))
        subscriber_draw = value(subscriber_sql)
        expect(subscriber_draw is not None, 'Current subscriber and settings revision can commit')
        expect(value(subscriber_sql)['draw_id'] == subscriber_draw['draw_id'],
               'Committed draw replay wins over its now-stale revision')
        metrics = value('SELECT public.koi_operational_metrics();')
        expect(metrics['events_received'] == 4 and metrics['events_duplicate'] == 11 and metrics['events_retried'] == 2,
               'Durable aggregate counters reflect processing')
        sql("INSERT INTO koipond_internal.events(message_id,channel_id,state,finished_at) "
            "SELECT 'expired-'||g,'channel-a','done',clock_timestamp()-interval '25 hours' FROM pg_catalog.generate_series(1,101) g;")
        enqueue('retention-trigger', 'retention-channel')
        expect(value("SELECT count(*) FROM koipond_internal.events WHERE message_id LIKE 'expired-%';") == 1,
               'Tombstone cleanup bounded to one hundred rows per ingress')
        sql("INSERT INTO koipond_internal.presence(subject_hash,streamer,seen_at) "
            "SELECT md5(g::text)||md5(g::text),false,clock_timestamp()-interval '2 hours' FROM pg_catalog.generate_series(1,101) g;")
        sql(as_service("SELECT public.koi_presence_touch('%s',false)" % ('b' * 64)))
        expect(value("SELECT count(*) FROM koipond_internal.presence WHERE seen_at<clock_timestamp()-interval '1 hour';") == 1,
               'Presence cleanup bounded to one hundred rows per touch')
        missing_settings = str(uuid.uuid4())
        sql("INSERT INTO public.streamers(id,twitch_id,twitch_login,twitch_display_name) "
            "VALUES('%s','missing-settings-channel','missing_settings','Missing Settings');" % missing_settings)
        enqueue('missing-settings-event', 'missing-settings-channel')
        sql("INSERT INTO public.settings(streamer_id) VALUES('%s');" % missing_settings)
        expect(value("SELECT context FROM koipond_internal.events WHERE message_id='missing-settings-event';") ==
               {'source': 'ingress', 'ignored': True}, 'Missing settings at receipt cannot adopt a later-created round')
        # Keep transaction A open after assigning seq N. B must not commit seq
        # N+1 or become visible to a worker before A commits on the same channel.
        sql("TRUNCATE koipond_internal.events,koipond_internal.channels RESTART IDENTITY; "
            "INSERT INTO koipond_internal.channels(channel_id) VALUES('ordering-channel');")
        process = subprocess.Popen(['docker', 'exec', '-i', name, 'psql', '-X', '-q', '-A', '-t',
                                    '-v', 'ON_ERROR_STOP=1', '-U', 'postgres', '-d', 'postgres'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b"BEGIN; SELECT public.koi_enqueue_event('ordering-a','ordering-channel','{}'); SELECT 'A_OPEN';\n")
            process.stdin.flush()
            output = b''
            deadline = time.monotonic() + 10
            while b'A_OPEN' not in output:
                remaining = deadline - time.monotonic()
                assert remaining > 0 and select.select([process.stdout], [], [], remaining)[0], 'Open enqueue transaction timed out'
                chunk = os.read(process.stdout.fileno(), 4096)
                assert chunk, 'Open enqueue transaction ended unexpectedly'
                output += chunk
            pool = ThreadPoolExecutor(max_workers=1)
            try:
                later = pool.submit(enqueue, 'ordering-b', 'ordering-channel')
                try:
                    later.result(timeout=0.5)
                    raise AssertionError('Later same-channel enqueue overtook an uncommitted event')
                except FutureTimeout:
                    expect(True, 'Channel lock serializes enqueue sequence and commit visibility')
                expect(claim(str(uuid.uuid4())) == [], 'Claim cannot overtake the open ingress transaction')
                process.stdin.write(b'COMMIT;\n\\q\n')
                process.stdin.close()
                process.stdin = None
                process.communicate(timeout=10)
                expect(process.returncode == 0 and later.result(timeout=10) == 't', 'Both serialized enqueues commit normally')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)
                pool.shutdown(wait=True)
            ordering_owner = str(uuid.uuid4())
            ordering_job = claim(ordering_owner)[0]
            expect(ordering_job['message_id'] == 'ordering-a', 'First committed channel event keeps first place')
            finish(ordering_job, ordering_owner)
            expect(claim(str(uuid.uuid4()))[0]['message_id'] == 'ordering-b', 'Second ingress follows the first')
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        print(json.dumps({'result': 'PASS', 'checks': checks, 'migrations': len(migrations),
                          'network': 'none', 'productionCredentials': False, 'productionRows': False}), flush=True)
    finally:
        if created:
            command(['docker', 'rm', '-f', name], timeout=30)


if __name__ == '__main__':
    main()
