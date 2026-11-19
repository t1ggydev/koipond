#!/usr/bin/env python3
"""Controller-only encrypted snapshot restore into the prepared private stack."""
import base64, gzip, hashlib, importlib.util, json, os, subprocess
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
os.umask(0o077)
root=Path('/home/leon/homelab-ansible/root-infra')
spec=importlib.util.spec_from_file_location('helper',root/'files/sophos-api.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
d=root/'recovery/koipond'
cipher=(d/'Koipond-Sicherung-2026-11-19.aesgcm.json').read_bytes()
record=json.loads(m.vault(['view',str(d/'Koipond-Sicherung-Schluessel-2026-11-19.vault')]))
assert hashlib.sha256(cipher).hexdigest()==record['sha256']
e=json.loads(cipher)
payload=json.loads(gzip.decompress(AESGCM(base64.b64decode(record['key'])).decrypt(base64.b64decode(e['nonce']),base64.b64decode(e['ciphertext'])+base64.b64decode(e['tag']),b'koipond-application-backup-v1')))
assert payload['format']=='koipond-application-backup-v1'
remote=r'''
import json,subprocess
from pathlib import Path
ROOT=Path('/srv/koipond-supabase')
def sql(q):
 p=subprocess.run(['docker','exec','-i','koipond-supabase-db','psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres','-d','postgres'],input='SET log_min_error_statement=panic; SET log_statement=none;\n'+q,capture_output=True,text=True)
 if p.returncode:raise RuntimeError('Private database restore step failed; sensitive output suppressed')
 return p.stdout.strip()
def restore(text):
 snapshot=json.loads(text)
 assert snapshot['project']=='xlatvbaiwfftkxgtzrly'
 tables=['participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages']
 assert set(snapshot['tables'])==set(tables)
 cols={t:json.loads(sql("SELECT json_agg(column_name ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='"+t+"' AND column_name <> 'source_event_id';")) for t in tables}
 assert sum(map(len,cols.values()))==85
 def manifest(t):
  return json.loads(sql("SELECT jsonb_build_object('rows',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t)-'source_event_id' order by ((to_jsonb(t)-'source_event_id')::text) collate \"C\"),'[]'::jsonb)::text)) FROM public."+t+' t;'))
 before={t:manifest(t) for t in tables}
 seed_only=(all(v['rows']==0 for t,v in before.items() if t!='settings') and before['settings']['rows']==1 and sql("SELECT count(*) FROM public.settings WHERE id='default' AND streamer_id IS NULL;")=='1')
 if any(v['rows'] for v in before.values()) and not seed_only:
  assert before==snapshot['manifest'], 'Existing private application data differs; refusing overwrite'
 else:
  q="BEGIN; TRUNCATE "+','.join('public.'+t for t in tables)+"; CREATE TEMP TABLE restore_payload(data jsonb); INSERT INTO restore_payload VALUES ('"+text.replace("'","''")+"');"
  for t in tables:
   columns=','.join('"'+c+'"' for c in cols[t])
   q+='INSERT INTO public.'+t+'('+columns+') SELECT '+columns+' FROM jsonb_populate_recordset(NULL::public.'+t+",(SELECT data->'tables'->'"+t+"' FROM restore_payload));"
  sql(q+'COMMIT;')
 after={t:manifest(t) for t in tables}
 assert after==snapshot['manifest'], 'Private restore manifest mismatch'
 result={'result':'PASS','snapshotAt':snapshot['snapshot_at'],'tables':after,'baselineColumns':85,'migrations':int(sql('SELECT count(*) FROM koipond_internal.deployment_migrations;')),'platformSchemasPreserved':True,'privateQueuePreserved':True}
 (ROOT/'restore-verification.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
'''
script=remote+'\nimport base64\nrestore(base64.b64decode('+repr(base64.b64encode(payload['snapshotJson'].encode()).decode())+').decode())\n'
p=subprocess.run(m.SSH+['infra@10.10.10.30','sudo python3 -'],input=script,capture_output=True,text=True,timeout=180)
if p.returncode:raise RuntimeError('Private Supabase restore failed; sensitive output suppressed')
result=json.loads(p.stdout)
result['backupSha256']=record['sha256']
(d/'supabase-restore-verification.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))

