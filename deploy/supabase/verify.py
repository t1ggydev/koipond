#!/usr/bin/env python3
"""Non-sensitive health/API evidence; keys are read internally, never printed."""
import json
from pathlib import Path
import subprocess
import urllib.request
import urllib.error

ROOT = Path('/srv/koipond-supabase')
env = dict(line.split('=', 1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
compose = ['docker', 'compose', '--env-file', '.env', '-f', 'compose.yml', '-f', 'images.lock.yml']
containers = subprocess.run(compose + ['ps', '--format', 'json'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
items = []
try:
    parsed = json.loads(containers)
    items = parsed if isinstance(parsed, list) else [parsed]
except json.JSONDecodeError:
    items = [json.loads(line) for line in containers.splitlines() if line.strip()]
statuses = [{'service': row['Service'], 'state': row['State'], 'health': row.get('Health', '')} for row in items]
assert len(statuses) == 11 and all(row['state'] == 'running' and row['health'] in ['healthy', ''] for row in statuses), statuses


def request(path, role='anon', payload=None):
    key = env['SERVICE_ROLE_KEY'] if role == 'service_role' else env['ANON_KEY']
    req = urllib.request.Request('http://10.10.10.30:8000' + path,
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = response.read()
            return response.status, data
    except urllib.error.HTTPError as error:
        error.read()
        return error.code, b''


checks = {}
for name, path in [('auth', '/auth/v1/health'), ('rest', '/rest/v1/streamers?select=id&limit=0'), ('storage', '/storage/v1/status')]:
    status, _ = request(path)
    checks[name] = status
    assert status == 200, (name, status)
for path in ['/', '/pg/', '/mcp', '/api/mcp']:
    status, _ = request(path)
    checks['private_route_blocked_' + path] = status
    assert status == 404, (path, status)
status, data = request('/rest/v1/rpc/koi_operational_metrics', 'service_role', {})
checks['service_role_operational_rpc'] = status
assert status == 200
assert 'queue_pending' in json.loads(data)
status, _ = request('/rest/v1/rpc/koi_operational_metrics', 'anon', {})
checks['anonymous_operational_rpc_denied'] = status
assert status in [401, 403]
db = subprocess.run(compose + ['exec', '-T', 'db', 'psql', '-X', '-q', '-A', '-t', '-U', 'postgres', '-d', 'postgres',
                               '-c', "SELECT json_build_object('version',current_setting('server_version'),'app_tables',(select count(*) from pg_catalog.pg_tables where schemaname='public' and tablename in ('participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages')),'applied_migrations',(select count(*) from koipond_internal.deployment_migrations));"],
                    cwd=ROOT, capture_output=True, text=True, check=True)
evidence = {'services': statuses, 'api_checks': checks, 'database': json.loads(db.stdout),
            'public_api_url': env['SUPABASE_PUBLIC_URL'], 'studio_binding': '127.0.0.1:3001',
            'api_binding': '10.10.10.30:8000', 'logflare_vector_enabled': False}
(ROOT / 'verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(evidence))

