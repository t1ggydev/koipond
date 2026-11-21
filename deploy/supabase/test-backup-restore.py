#!/usr/bin/env python3
"""Restore local full-stack dumps into disposable PostgreSQL17 with no network."""
import hashlib,json,os,subprocess,tarfile,tempfile,time,uuid
from pathlib import Path
os.umask(0o077)
root=Path('/srv/koipond-supabase');archive=Path('/var/backups/koipond-supabase/latest.tar.gz').resolve()
name='koipond-private-restore-'+uuid.uuid4().hex[:12]
image=json.loads((root/'images.lock.yml').read_text())['services']['db']['image']
def run(args,**kw):
 p=subprocess.run(args,capture_output=True,**kw)
 if p.returncode:raise RuntimeError('Isolated full-stack recovery command failed; sensitive output suppressed')
 return p.stdout
created=False
try:
 with tempfile.TemporaryDirectory(prefix='koipond-restore-',dir='/var/backups/koipond-supabase') as tmp:
  directory=Path(tmp)
  with tarfile.open(archive) as tar:tar.extractall(directory,filter='data')
  manifest=json.loads((directory/'manifest.json').read_text())
  for filename,digest in manifest['files'].items():assert hashlib.sha256((directory/filename).read_bytes()).hexdigest()==digest
  run(['docker','run','--rm','-d','--name',name,'--network','none','--log-driver','none','--memory','1536m','--cpus','2','--user','postgres','--tmpfs','/restore:rw,mode=1777,size=768m','--entrypoint','/bin/bash',image,'-c','initdb -D /restore/pg -U supabase_admin -A trust >/dev/null && exec postgres -D /restore/pg -c unix_socket_directories=/restore -c listen_addresses= -c shared_preload_libraries=pg_net,pg_stat_statements -c log_min_error_statement=panic -c log_statement=none'])
  created=True
  for _ in range(45):
   p=subprocess.run(['docker','exec',name,'pg_isready','-h','/restore','-U','supabase_admin'],capture_output=True)
   if p.returncode==0:break
   time.sleep(1)
  else:raise RuntimeError('Isolated PostgreSQL17 did not start')
  def sql(q,db='postgres'):
   return run(['docker','exec','-i',name,'psql','-XqAt','-h','/restore','-U','supabase_admin','-d',db,'-v','ON_ERROR_STOP=1'],input=q.encode()).decode().strip()
  globals_sql=(directory/'globals.sql').read_text()
  # Use the source superuser name to preserve Supabase role-grant restrictions.
  globals_sql=globals_sql.replace('CREATE ROLE supabase_admin;','')
  sql(globals_sql)
  for db in manifest['databases']:
   if db!='postgres':sql('CREATE DATABASE "'+db+'";')
   run(['docker','exec','-i',name,'pg_restore','-h','/restore','-U','supabase_admin','-d',db,'--exit-on-error','--no-tablespaces'],input=(directory/(db+'.dump')).read_bytes())
  tables=['participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages']
  counts={t:int(sql('SELECT count(*) FROM public.'+t+';')) for t in tables}
  source={t:int(run(['docker','exec','koipond-supabase-db','psql','-XqAt','-U','postgres','-c','SELECT count(*) FROM public.'+t+';']).decode()) for t in tables}
  assert counts==source
  checks=json.loads(sql("SELECT json_build_object('migrations',(select count(*) from koipond_internal.deployment_migrations),'auth_schema',exists(select 1 from pg_namespace where nspname='auth'),'storage_schema',exists(select 1 from pg_namespace where nspname='storage'),'realtime_schema',exists(select 1 from pg_namespace where nspname='_realtime'),'anonymous_queue_execute',has_function_privilege('anon','public.koi_operational_metrics()','EXECUTE'));"))
  assert checks['migrations']==17 and checks['auth_schema'] and checks['storage_schema'] and checks['realtime_schema'] and not checks['anonymous_queue_execute']
  result={'result':'PASS','archive':str(archive),'databaseCount':len(manifest['databases']),'appRows':counts,'checks':checks,'network':'none','hostPorts':False,'data':'tmpfs','globalsRestored':True,'configurationHashesVerified':True}
  (root/'full-restore-verification.json').write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result))
finally:
 if created:subprocess.run(['docker','rm','-f',name],capture_output=True)




