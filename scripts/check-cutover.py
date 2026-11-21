#!/usr/bin/env python3
"""Verify reversible Cloud write fencing using synthetic, isolated PostgreSQL only."""
import json
from pathlib import Path
import subprocess
import time
import uuid

root = Path(__file__).resolve().parent.parent
name = 'koipond-cutover-check-' + uuid.uuid4().hex[:12]
image = 'postgres:17-bookworm@sha256:837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b'
def command(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)
def sql(query, required=True):
    result = command(['docker','exec','-i',name,'psql','-XqAt','-v','ON_ERROR_STOP=1','-U','postgres'],input=query)
    if required and result.returncode:
        raise RuntimeError('Isolated cutover SQL check failed: ' + result.stderr[:300])
    return result
checks = 0
try:
    command(['docker','run','--rm','-d','--name',name,'--network','none',
             '--tmpfs','/var/lib/postgresql/data:rw,noexec,nosuid,size=256m',
             '--memory','768m','--cpus','1','-e','POSTGRES_HOST_AUTH_METHOD=trust',
             image,'-c','wal_level=logical'],check=True)
    for _ in range(60):
        if (command(['docker','exec',name,'cat','/proc/1/comm']).stdout.strip() == 'postgres' and
                command(['docker','exec',name,'pg_isready','-U','postgres']).returncode == 0):
            break
        time.sleep(1)
    else:
        raise RuntimeError('Isolated database startup failed')
    sql('CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; CREATE PUBLICATION supabase_realtime;')
    baseline = sorted(p for p in (root/'supabase/migrations').glob('*.sql') if int(p.name[:4]) <= 16)
    assert len(baseline) == 16
    for migration in baseline:
        sql('BEGIN;\n' + migration.read_text() + '\nCOMMIT;')
    probe = "BEGIN; SET LOCAL ROLE service_role; INSERT INTO draws(triggered_by) VALUES('dashboard'); ROLLBACK;"
    sql(probe); checks += 1
    before = sql("SELECT md5(string_agg(policyname||qual,',' ORDER BY policyname)) FROM pg_policies WHERE schemaname='public';").stdout
    sql((root/'deploy/cutover/freeze-cloud.sql').read_text()); checks += 1
    denied = sql(probe, required=False)
    assert denied.returncode and 'permission denied' in denied.stderr; checks += 1
    assert sql('BEGIN;SET LOCAL ROLE service_role;SELECT count(*) FROM draws;ROLLBACK;').returncode == 0; checks += 1
    after = sql("SELECT md5(string_agg(policyname||qual,',' ORDER BY policyname)) FROM pg_policies WHERE schemaname='public';").stdout
    assert before == after; checks += 1
    sql((root/'deploy/cutover/resume-cloud.sql').read_text()); checks += 1
    sql(probe); checks += 1
    sql('BEGIN;\n'+(root/'supabase/migrations/0017_operational_queue.sql').read_text()+'\nCOMMIT;')
    assert sql((root/'deploy/cutover/freeze-cloud.sql').read_text(), required=False).returncode != 0; checks += 1
    print(json.dumps({'result':'PASS','checks':checks,'network':'none','hostPorts':0,
                      'source':'synthetic only','cloudConnections':False}))
finally:
    command(['docker','rm','-f',name])
