#!/usr/bin/env python3
"""Controller helper. Receive the recovery key over SSH stdin; never print it."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

os.umask(0o077)
root = Path('/home/leon/homelab-ansible/root-infra')
spec = importlib.util.spec_from_file_location('vaulthelper',root/'files/sophos-api.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
record = json.load(sys.stdin)
assert record['format'] == 'koipond-application-backup-v1'
assert len(base64.b64decode(record['key'],validate=True)) == 32
label = record['label']
assert re.fullmatch(r'[A-Za-z0-9_-]+',label)
source = root/'work'/('Koipond-Sicherung-'+label+'.aesgcm.json')
data = source.read_bytes()
assert hashlib.sha256(data).hexdigest() == record['sha256']
directory = root/'recovery/koipond'
directory.mkdir(mode=0o700,exist_ok=True)
target = directory/source.name
if target.exists():
    assert target.read_bytes() == data, 'Refusing to overwrite a different snapshot'
else:
    partial = directory/('.'+source.name+'.partial')
    partial.write_bytes(data)
    partial.replace(target)
target.chmod(0o600)
keyfile = directory/('Koipond-Sicherung-Schluessel-'+label+'.vault')
if keyfile.exists():
    previous = json.loads(helper.vault(['view',str(keyfile)]))
    assert previous['key'] == record['key'] and previous['sha256'] == record['sha256']
else:
    partial = directory/('.'+keyfile.name+'.partial')
    helper.vault(['encrypt','--output',str(partial),'-'],data=json.dumps(record).encode())
    partial.chmod(0o600)
    partial.replace(keyfile)
print(json.dumps({'result':'PASS','backup':str(target),'keyVault':str(keyfile),
                  'sha256':record['sha256'],'secretOutput':False}))
