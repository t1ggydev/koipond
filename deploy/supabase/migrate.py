#!/usr/bin/env python3
"""Apply only local Koipond migrations; never accepts an external DSN."""
import hashlib
from pathlib import Path
import subprocess

ROOT = Path('/srv/koipond-supabase')


def sql(query):
    result = subprocess.run(['docker', 'compose', '--env-file', '.env', '-f', 'compose.yml', '-f', 'images.lock.yml',
                             'exec', '-T', 'db', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1',
                             '-U', 'postgres', '-d', 'postgres'], cwd=ROOT, input=query,
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Local database migration command failed: ' + result.stderr[-500:])
    return result.stdout.strip()


assert ROOT.is_dir() and (ROOT / '.env').is_file()
sql("CREATE SCHEMA IF NOT EXISTS koipond_internal; "
    "CREATE TABLE IF NOT EXISTS koipond_internal.deployment_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz DEFAULT now()); "
    "ALTER TABLE koipond_internal.deployment_migrations ENABLE ROW LEVEL SECURITY; "
    "REVOKE ALL ON SCHEMA koipond_internal FROM PUBLIC,anon,authenticated; "
    "REVOKE ALL ON koipond_internal.deployment_migrations FROM PUBLIC,anon,authenticated;")
applied = 0
for path in sorted((ROOT / 'app-migrations').glob('*.sql')):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    old = sql("SELECT sha256 FROM koipond_internal.deployment_migrations WHERE name='" + path.name + "';")
    if old:
        assert old == digest, 'Previously applied migration checksum differs: ' + path.name
        continue
    sql("BEGIN;\n" + path.read_text() + "\nINSERT INTO koipond_internal.deployment_migrations(name,sha256) VALUES ('" + path.name + "','" + digest + "');\nCOMMIT;")
    applied += 1
print('Applied local Koipond migrations: ' + str(applied))
