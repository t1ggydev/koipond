#!/usr/bin/env python3
"""Exercise only the final-sync SQL generator in a disposable restored PG17."""
from pathlib import Path
root=Path(__file__).parent
snippet=r'''
  # Everything below targets only the disposable, network-isolated database.
  columns={t:json.loads(sql("SELECT json_agg(column_name ORDER BY ordinal_position) FROM information_schema.columns WHERE table_schema='public' AND table_name='"+t+"' AND column_name<>'source_event_id';")) for t in TABLES}
  table_json={t:json.loads(sql("SELECT coalesce(json_agg(to_jsonb(t)-'source_event_id'),'[]'::json) FROM public."+t+' t;')) for t in TABLES}
  manifests={t:json.loads(sql("SELECT jsonb_build_object('rows',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t)-'source_event_id' order by ((to_jsonb(t)-'source_event_id')::text) collate \"C\"),'[]'::jsonb)::text)) FROM public."+t+' t;')) for t in TABLES}
  snapshot={'project':'xlatvbaiwfftkxgtzrly','write_fence':True,'tables':table_json,'manifest':manifests}
  text=json.dumps(snapshot)
  statement=restore_sql(text,columns,True)
  assert 'deployment_migrations' not in statement and 'TRUNCATE auth.' not in statement and 'TRUNCATE storage.' not in statement
  assert 'TRUNCATE koipond_internal.events' in statement
  bad=json.loads(text);bad['write_fence']=False
  try:restore_sql(json.dumps(bad),columns,True)
  except AssertionError:pass
  else:raise AssertionError('Missing write fence was accepted')
  bad=json.loads(text);bad['manifest']['draws']['md5']='00000000000000000000000000000000'
  try:sql(restore_sql(json.dumps(bad),columns,True))
  except RuntimeError:pass
  else:raise AssertionError('Bad manifest was accepted')
  assert int(sql('SELECT count(*) FROM public.draws;'))==counts['draws']
  sql("INSERT INTO koipond_internal.leases(name,owner,expires_at) VALUES('final-sync-fixture',gen_random_uuid(),now()+interval '1 minute'); UPDATE koipond_internal.counters SET received=99;")
  sql(statement)
  assert int(sql('SELECT count(*) FROM koipond_internal.leases;'))==0
  assert int(sql('SELECT received FROM koipond_internal.counters;'))==0
  assert int(sql('SELECT count(*) FROM koipond_internal.deployment_migrations;'))==17
  assert all(int(sql('SELECT count(*) FROM public.'+t+';'))==counts[t] for t in TABLES)
'''
source=(root/'test-backup-restore.py').read_text()
marker="  result={'result':'PASS'"
assert marker in source
source=source.replace(marker,snippet+"  checks['finalSyncSQLGenerator']='PASS'\n"+marker)
source=source.replace("full-restore-verification.json","final-sync-generator-verification.json")
exec(compile((root/'restore_sql.py').read_text()+'\n'+source,'isolated-final-sync-check','exec'))
