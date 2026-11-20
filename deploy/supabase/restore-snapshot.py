#!/usr/bin/env python3
"""Controller-only application restore; final replacement requires explicit approval gates."""
import argparse,base64,gzip,hashlib,importlib.util,json,os,subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
os.umask(0o077)
root=Path('/home/leon/homelab-ansible/root-infra');d=root/'recovery/koipond'
p=argparse.ArgumentParser()
p.add_argument('--backup',type=Path,default=d/'Koipond-Sicherung-2026-11-19.aesgcm.json')
p.add_argument('--key-vault',type=Path,default=d/'Koipond-Sicherung-Schluessel-2026-11-19.vault')
p.add_argument('--go-live-approved',action='store_true')
p.add_argument('--final-sync',action='store_true')
args=p.parse_args()
if args.final_sync != args.go_live_approved:p.error('Final sync requires both --final-sync and --go-live-approved')
for path in [args.backup,args.key_vault]:
 if not path.resolve().is_relative_to(d.resolve()) or not path.is_file():p.error('Backup and key Vault must be existing files under recovery/koipond')
spec=importlib.util.spec_from_file_location('helper',root/'files/sophos-api.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cipher=args.backup.read_bytes()
record=json.loads(m.vault(['view',str(args.key_vault.resolve())]))
assert hashlib.sha256(cipher).hexdigest()==record['sha256']
e=json.loads(cipher)
payload=json.loads(gzip.decompress(AESGCM(base64.b64decode(record['key'])).decrypt(base64.b64decode(e['nonce']),base64.b64decode(e['ciphertext'])+base64.b64decode(e['tag']),b'koipond-application-backup-v1')))
assert payload['format']=='koipond-application-backup-v1'
for migration in payload['schemaMigrations']:assert hashlib.sha256(migration['sql'].encode()).hexdigest()==migration['sha256']
snapshot=json.loads(payload['snapshotJson'])
if args.final_sync:assert snapshot.get('write_fence') is True, 'Final snapshot requires a verified Cloud write fence'
def ssh(host,code):
 result=subprocess.run(m.SSH+['infra@'+host,'sudo python3 -'],input=code,capture_output=True,text=True,timeout=180)
 if result.returncode:raise RuntimeError('Private restore prerequisite/operation failed; sensitive output suppressed')
 return result.stdout
quiesce_code=r'''
import json,subprocess,stat
from pathlib import Path
marker=Path('/srv/koipond-staging/final-sync-quiesced')
assert marker.is_file() and marker.stat().st_uid==0 and stat.S_IMODE(marker.stat().st_mode)==0o600,'Missing protected final-sync marker'
for name in ['koipond-stats-refresh','koipond-production-stats-refresh']:
 for suffix in ['timer','service']:
  r=subprocess.run(['systemctl','is-active',name+'.'+suffix],capture_output=True,text=True)
  assert r.stdout.strip() in ['inactive','failed','unknown'],'Statistics writer/scheduler still active'
ids=subprocess.run(['docker','ps','-q'],capture_output=True,text=True,check=True).stdout.split()
if ids:
 rows=json.loads(subprocess.run(['docker','inspect']+ids,capture_output=True,text=True,check=True).stdout)
 assert not any(row['Config'].get('Labels',{}).get('com.docker.compose.project') in ['koipond-staging','koipond-production'] for row in rows),'Stage or production container is still running'
print('Quiescence verified')
'''
if args.final_sync:
 ssh('10.10.10.20',quiesce_code)
 backup=json.loads(ssh('10.10.10.30',"import subprocess\np=subprocess.run(['python3','/srv/koipond-supabase/backup.py'],capture_output=True,text=True);assert p.returncode==0;print(p.stdout)"))
 assert backup['result']=='PASS'
 ssh('10.10.10.20',quiesce_code)
generator=(Path(__file__).parent/'restore_sql.py').read_text()
remote=generator+r'''
import json,subprocess
from pathlib import Path
ROOT=Path('/srv/koipond-supabase')
def sql(q):
 p=subprocess.run(['docker','exec','-i','koipond-supabase-db','psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres','-d','postgres'],input='SET log_min_error_statement=panic; SET log_statement=none;\n'+q,capture_output=True,text=True)
 if p.returncode:raise RuntimeError('Private database restore step failed; sensitive output suppressed')
 return p.stdout.strip()
def restore(text,final_sync):
 snapshot=json.loads(text)
 columns={t:json.loads(sql("SELECT json_agg(column_name ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='"+t+"' AND column_name <> 'source_event_id';")) for t in TABLES}
 statement=restore_sql(text,columns,final_sync)
 def manifest(t):
  return json.loads(sql("SELECT jsonb_build_object('rows',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t)-'source_event_id' order by ((to_jsonb(t)-'source_event_id')::text) collate \"C\"),'[]'::jsonb)::text)) FROM public."+t+' t;'))
 before={t:manifest(t) for t in TABLES}
 seed_only=(all(v['rows']==0 for t,v in before.items() if t!='settings') and before['settings']['rows']==1 and sql("SELECT count(*) FROM public.settings WHERE id='default' AND streamer_id IS NULL;")=='1')
 if not final_sync and any(v['rows'] for v in before.values()) and not seed_only:
  assert before==snapshot['manifest'],'Existing private application data differs; refusing overwrite'
 else:sql(statement)
 after={t:manifest(t) for t in TABLES}
 assert after==snapshot['manifest'],'Private restore manifest mismatch'
 result={'result':'PASS','snapshotAt':snapshot['snapshot_at'],'tables':after,'baselineColumns':85,'migrations':int(sql('SELECT count(*) FROM koipond_internal.deployment_migrations;')),'platformSchemasPreserved':True,'privateQueuePreserved':not final_sync,'finalSync':final_sync}
 (ROOT/('final-sync-verification.json' if final_sync else 'restore-verification.json')).write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
'''
script=remote+'\nimport base64\nrestore(base64.b64decode('+repr(base64.b64encode(payload['snapshotJson'].encode()).decode())+').decode(),'+repr(args.final_sync)+')\n'
result=json.loads(ssh('10.10.10.30',script))
result['backupSha256']=record['sha256']
if args.final_sync:result['preSyncLocalBackup']=backup['archive']
(d/('supabase-final-sync-verification.json' if args.final_sync else 'supabase-restore-verification.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
