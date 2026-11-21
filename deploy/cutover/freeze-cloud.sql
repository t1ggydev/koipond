-- APPROVAL REQUIRED. Only the original eight-table Cloud database, never the new stack.
-- Prevent old/cached Vercel clients from writing while the final snapshot is taken.
BEGIN;
SET LOCAL lock_timeout = '15s';
SET LOCAL statement_timeout = '30s';
DO $$
DECLARE t text; r text; p text;
BEGIN
  IF (SELECT count(*) FROM pg_tables WHERE schemaname='public') <> 8 OR
     EXISTS (SELECT FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
             WHERE n.nspname='public' AND p.prosecdef) OR
     pg_has_role('service_role','postgres','member') THEN
    RAISE EXCEPTION 'Unexpected Cloud baseline; stop and review';
  END IF;
  FOREACH t IN ARRAY ARRAY['participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages'] LOOP
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
      FOREACH p IN ARRAY ARRAY['INSERT','UPDATE','DELETE','TRUNCATE'] LOOP
        IF NOT has_table_privilege(r,format('public.%I',t),p) THEN
          RAISE EXCEPTION 'Original grants differ or already frozen; stop and review';
        END IF;
      END LOOP;
    END LOOP;
  END LOOP;
END $$;
LOCK TABLE public.participants, public.streamers, public.osu_stats, public.settings,
  public.presets, public.entries, public.draws, public.winner_messages IN SHARE MODE;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  public.participants, public.streamers, public.osu_stats, public.settings,
  public.presets, public.entries, public.draws, public.winner_messages
  FROM anon, authenticated, service_role;
DO $$
DECLARE t text; r text;
BEGIN
  FOREACH t IN ARRAY ARRAY['participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages'] LOOP
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
      IF has_table_privilege(r,format('public.%I',t),'INSERT,UPDATE,DELETE,TRUNCATE') THEN
        RAISE EXCEPTION 'Write fence incomplete; transaction rolled back';
      END IF;
    END LOOP;
  END LOOP;
END $$;
COMMIT;
