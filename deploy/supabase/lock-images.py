#!/usr/bin/env python3
"""Resolve reviewed image tags once; subsequent deployments use image digests."""
import json
from pathlib import Path
import subprocess

ROOT = Path('/srv/koipond-supabase')
lock = ROOT / 'images.lock.yml'
if lock.exists():
    print('Existing image digest lock retained')
    raise SystemExit(0)
result = subprocess.run(['docker', 'compose', '--env-file', '.env', '-f', 'compose.yml', 'config', '--format', 'json'],
                        cwd=ROOT, capture_output=True, text=True, check=True)
config = json.loads(result.stdout)
images = {}
for name, service in config['services'].items():
    image = service['image']
    inspection = json.loads(subprocess.run(['docker', 'image', 'inspect', image], capture_output=True, text=True, check=True).stdout)[0]
    repository = image.rsplit(':', 1)[0]
    matches = [digest for digest in inspection['RepoDigests'] if digest.split('@', 1)[0] == repository]
    assert len(matches) == 1, 'Could not identify immutable image digest for ' + name
    images[name] = {'image': matches[0]}
lock.write_text(json.dumps({'services': images}, indent=2) + '\n')
print('Immutable image digests recorded for ' + str(len(images)) + ' services')
