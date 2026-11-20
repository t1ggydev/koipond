#!/usr/bin/env python3
"""Pure SQL generator shared by initial restore and explicitly approved final sync."""
import json,re
TABLES=('participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages')
OPERATIONAL=('events','channels','presence','leases','task_runs','counters')
def restore_sql(text, columns, final_sync=False):
 snapshot=json.loads(text)
 assert snapshot['project']=='xlatvbaiwfftkxgtzrly'
 assert set(snapshot['tables'])==set(TABLES) and set(snapshot['manifest'])==set(TABLES)
 assert sum(map(len,columns.values()))==85
 assert set(columns)==set(TABLES)
 if final_sync:assert snapshot.get('write_fence') is True, 'Final snapshot lacks source write fence'
 for t in TABLES:
  assert all(re.fullmatch('[a-z_][a-z0-9_]*',c) for c in columns[t])
  assert isinstance(snapshot['tables'][t],list)
  assert snapshot['manifest'][t]['rows']==len(snapshot['tables'][t])
  for row in snapshot['tables'][t]:assert set(row)==set(columns[t]), 'Snapshot baseline columns differ'
 escaped=text.replace("'","''")
 q="BEGIN; SET LOCAL lock_timeout='10s'; SET LOCAL statement_timeout='90s'; LOCK TABLE "+','.join('public.'+t for t in TABLES)+" IN ACCESS EXCLUSIVE MODE; CREATE TEMP TABLE restore_payload(data jsonb) ON COMMIT DROP; INSERT INTO restore_payload VALUES ('"+escaped+"');"
 for t in TABLES:
  names=','.join('"'+c+'"' for c in columns[t])
  q+='CREATE TEMP TABLE check_'+t+' (LIKE public.'+t+') ON COMMIT DROP;'
  q+='INSERT INTO check_'+t+'('+names+') SELECT '+names+' FROM jsonb_populate_recordset(NULL::public.'+t+",(SELECT data->'tables'->'"+t+"' FROM restore_payload));"
  manifest="jsonb_build_object('rows',count(*),'md5',md5(coalesce(jsonb_agg(to_jsonb(t)-'source_event_id' order by ((to_jsonb(t)-'source_event_id')::text) collate \"C\"),'[]'::jsonb)::text))"
  q+="DO $check$ BEGIN IF (SELECT "+manifest+' FROM check_'+t+" t) IS DISTINCT FROM (SELECT data->'manifest'->'"+t+"' FROM restore_payload) THEN RAISE EXCEPTION 'Snapshot manifest mismatch: "+t+"'; END IF; END $check$;"
 q+='TRUNCATE '+','.join('public.'+t for t in TABLES)+';'
 for t in TABLES:
  names=','.join('"'+c+'"' for c in columns[t])
  q+='INSERT INTO public.'+t+'('+names+') SELECT '+names+' FROM check_'+t+';'
 if final_sync:
  q+='TRUNCATE '+','.join('koipond_internal.'+t for t in OPERATIONAL)+' RESTART IDENTITY; INSERT INTO koipond_internal.counters(singleton) VALUES(true);'
 return q+'COMMIT;'
