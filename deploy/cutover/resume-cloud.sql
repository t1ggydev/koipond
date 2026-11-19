-- APPROVAL REQUIRED. Restore the exact DML grants checked by freeze-cloud.sql.
-- Only use before new production writes, or after reconciliation back to Cloud.
BEGIN;
SET LOCAL lock_timeout = '15s';
DO $$
DECLARE t text; r text;
BEGIN
  IF (SELECT count(*) FROM pg_tables WHERE schemaname='public') <> 8 OR
     EXISTS (SELECT FROM pg_namespace WHERE nspname='koipond_internal') THEN
    RAISE EXCEPTION 'Not the original Cloud baseline';
  END IF;
  FOREACH t IN ARRAY ARRAY['participants','streamers','osu_stats','settings','presets','entries','draws','winner_messages'] LOOP
    FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
      IF has_table_privilege(r,format('public.%I',t),'INSERT,UPDATE,DELETE,TRUNCATE') THEN
        RAISE EXCEPTION 'Cloud was not completely frozen; stop and review';
      END IF;
    END LOOP;
  END LOOP;
END $$;
GRANT INSERT, UPDATE, DELETE, TRUNCATE ON TABLE
  public.participants, public.streamers, public.osu_stats, public.settings,
  public.presets, public.entries, public.draws, public.winner_messages
  TO anon, authenticated, service_role;
COMMIT;
