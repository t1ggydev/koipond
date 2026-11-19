#!/usr/bin/env python3
"""Root-only local stack backup; never touches Cloud or emits credentials."""
import datetime,fcntl,hashlib,json,os,shutil,subprocess,tarfile,tempfile,time
from pathlib import Path
os.umask(0o077)
root=Path('/srv/koipond-supabase'); destination=Path('/var/backups/koipond-supabase')
destination.mkdir(mode=0o700,parents=True,exist_ok=True)
lock=open(destination/'.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
metrics=Path('/var/lib/prometheus/node-exporter/koipond-supabase-backup.prom')
previous={}
if metrics.exists():
 for line in metrics.read_text().splitlines():
  parts=line.split()
  if len(parts)==2 and not parts[0].startswith('#'):
   try:previous[parts[0]]=float(parts[1])
   except ValueError:pass
last_success=previous.get('koipond_supabase_backup_last_success_timestamp_seconds',0)
last_size=previous.get('koipond_supabase_backup_size_bytes',0)
def publish_metrics(success, timestamp, size):
 values={'koipond_supabase_backup_last_success_timestamp_seconds':timestamp,'koipond_supabase_backup_size_bytes':size,'koipond_supabase_backup_last_run_success':success}
 body=''.join('# TYPE '+name+' gauge\n'+name+' '+str(value)+'\n' for name,value in values.items())
 fd,temp=tempfile.mkstemp(prefix='.koipond-supabase-',dir=metrics.parent)
 try:
  with os.fdopen(fd,'w') as output:
   output.write(body);output.flush();os.fsync(output.fileno());os.fchmod(output.fileno(),0o644)
  os.replace(temp,metrics)
 finally:
  if os.path.exists(temp):os.unlink(temp)
# A failed/interrupted run leaves success=0 while preserving last good age/size.
publish_metrics(0,last_success,last_size)
def run(args,**kw):
 p=subprocess.run(['docker','exec','koipond-supabase-db']+args,capture_output=True,**kw)
 if p.returncode:raise RuntimeError('Local database backup failed; sensitive output suppressed')
 return p.stdout
with tempfile.TemporaryDirectory(prefix='.creating-',dir=destination) as tmp:
 work=Path(tmp)
 (work/'globals.sql').write_bytes(run(['pg_dumpall','-U','postgres','--globals-only']))
 databases=run(['psql','-XAt','-U','postgres','-c',"SELECT datname FROM pg_database WHERE datallowconn AND NOT datistemplate ORDER BY 1;"]).decode().splitlines()
 assert set(databases)=={'postgres','_supabase'}
 for db in databases:(work/(db+'.dump')).write_bytes(run(['pg_dump','-U','postgres','-Fc','--dbname',db]))
 with tarfile.open(work/'files.tar.gz','w:gz') as tar:
  for name in ['.env','compose.yml','images.lock.yml','kong.private.yml','upstream','app-migrations','data/storage','data/snippets']:
   tar.add(root/name,arcname=name)
 manifest={'createdAt':stamp,'databases':databases,'files':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in work.iterdir()}}
 (work/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 target=destination/(stamp+'.tar.gz'); partial=destination/('.'+stamp+'.partial')
 with tarfile.open(partial,'w:gz') as tar:
  for p in sorted(work.iterdir()):tar.add(p,arcname=p.name)
 with open(partial,'rb') as file:os.fsync(file.fileno())
 partial.replace(target)
 latest=destination/'.latest.tmp'
 if latest.is_symlink():latest.unlink()
 latest.symlink_to(target.name);latest.replace(destination/'latest.tar.gz')
 for old in sorted(destination.glob('20*T*Z.tar.gz'))[:-7]:old.unlink()
 publish_metrics(1,time.time(),target.stat().st_size)
 print(json.dumps({'result':'PASS','archive':str(target),'databases':databases,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'storageFiles':sum(1 for p in (root/'data/storage').rglob('*') if p.is_file()),'retention':7}))

