#!/usr/bin/env python3
"""Collect bounded container diagnostics with all environment values redacted."""
import json
from pathlib import Path
import subprocess

ROOT = Path('/srv/koipond-supabase')
values = [line.split('=', 1)[1] for line in (ROOT / '.env').read_text().splitlines()
          if '=' in line and len(line.split('=', 1)[1]) >= 8]
cmd = ['docker', 'compose', '--env-file', '.env', '-f', 'compose.yml']
if (ROOT / 'images.lock.yml').exists():
    cmd += ['-f', 'images.lock.yml']
for args in [['ps', '--format', 'json'], ['logs', '--no-color', '--tail', '12']]:
    result = subprocess.run(cmd + args, cwd=ROOT, capture_output=True, text=True)
    text = result.stdout + result.stderr
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    print(text[-15000:])
