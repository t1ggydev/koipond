#!/usr/bin/env python3
"""Replay repository migrations in a disposable, network-isolated PostgreSQL.

No hosted database connections or production credentials are accepted.
Expected columns must contain schema metadata only, not application rows.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-columns", required=True)
    parser.add_argument("--expected-structure")
    args = parser.parse_args()
    expected = json.loads(Path(args.expected_columns).read_text())
    migrations = sorted((Path(__file__).resolve().parent.parent / "supabase/migrations").glob("*.sql"))
    assert len(migrations) == 16, "Review migration additions before changing this baseline check"
    name = "koipond-schema-check-" + uuid.uuid4().hex[:12]
    created = False

    def sql(query, check=True):
        return subprocess.run(
            ["docker", "exec", "-i", name, "psql", "-X", "-q", "-A", "-t",
             "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres"],
            input=query, text=True, capture_output=True, check=check, timeout=30,
        )

    try:
        # Trust is restricted to an ephemeral container with no network or host ports.
        run(["docker", "run", "--rm", "-d", "--name", name, "--network", "none",
             "--memory", "768m", "--cpus", "1", "--tmpfs",
             "/var/lib/postgresql/data:rw,noexec,nosuid,size=256m",
             "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "postgres:17-bookworm",
             "-c", "wal_level=logical"], timeout=180)
        created = True
        for _ in range(90):
            process = run(["docker", "exec", name, "cat", "/proc/1/comm"])
            ready = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"],
                                   capture_output=True)
            if process.stdout.strip() == "postgres" and ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable database failed to start")

        sql("CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; "
            "CREATE ROLE service_role NOLOGIN BYPASSRLS; "
            "CREATE PUBLICATION supabase_realtime;")
        for migration in migrations:
            sql("BEGIN;\n" + migration.read_text() + "\nCOMMIT;")

        query = """
        SELECT coalesce(json_agg(row_to_json(c)), '[]'::json) FROM (
          SELECT table_name,column_name,udt_name,is_nullable,column_default
          FROM information_schema.columns WHERE table_schema='public'
          ORDER BY table_name,ordinal_position
        ) c;
        """
        actual = json.loads(sql(query).stdout)
        key = lambda row: (row["table_name"], row["column_name"])
        expected_by_key = {key(row): row for row in expected}
        actual_by_key = {key(row): row for row in actual}
        missing = sorted(set(expected_by_key) - set(actual_by_key))
        extra = sorted(set(actual_by_key) - set(expected_by_key))
        changed = [list(k) for k in sorted(set(expected_by_key) & set(actual_by_key))
                   if any(expected_by_key[k][field] != actual_by_key[k][field]
                          for field in ["udt_name", "is_nullable", "column_default"])]
        assert not (missing or extra or changed), json.dumps({"missing": missing, "extra": extra, "changed": changed})

        structure_counts = {}
        if args.expected_structure:
            structure = json.loads(Path(args.expected_structure).read_text())
            queries = {
                "indexes": "SELECT tablename,indexname,indexdef FROM pg_catalog.pg_indexes "
                           "WHERE schemaname='public' ORDER BY tablename,indexname",
                "constraints": "SELECT c.relname AS table_name,k.conname AS constraint_name,"
                               "k.contype AS constraint_type,pg_catalog.pg_get_constraintdef(k.oid) AS definition "
                               "FROM pg_catalog.pg_constraint k JOIN pg_catalog.pg_class c ON c.oid=k.conrelid "
                               "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                               "WHERE n.nspname='public' ORDER BY c.relname,k.conname",
            }
            def normalize(rows):
                return [{field: re.sub(r"\bpublic\.", "", value) if isinstance(value, str) else value
                         for field, value in row.items()} for row in rows]
            for label, statement in queries.items():
                rows = json.loads(sql("SELECT coalesce(json_agg(row_to_json(q)), '[]'::json) FROM (" + statement + ") q;").stdout)
                assert normalize(rows) == normalize(structure[label]), label + " differ from hosted schema metadata"
                structure_counts[label] = len(rows)

        secured = sql("SELECT count(*) FROM pg_catalog.pg_tables WHERE "
                      "schemaname='public' AND rowsecurity;").stdout.strip()
        assert secured == "8", "All eight app tables must have RLS enabled"
        publication = sql("SELECT tablename FROM pg_catalog.pg_publication_tables "
                          "WHERE pubname='supabase_realtime' ORDER BY tablename;").stdout.strip()
        assert publication == "draws", "Realtime publication drift"
        sql("INSERT INTO public.participants(twitch_id,twitch_login,twitch_display_name,osu_id,osu_username) "
            "VALUES('isolated-test','isolated','Isolated',1,'Isolated');")
        visibility = sql("BEGIN; SET LOCAL ROLE anon; SELECT count(*) FROM public.participants; "
                         "SELECT count(*) FROM public.settings; ROLLBACK;").stdout.strip().splitlines()
        assert visibility == ["1", "0"], "Anonymous read policies do not match expected visibility"
        denied = sql("BEGIN; SET LOCAL ROLE anon; INSERT INTO public.settings(id) "
                     "VALUES('isolated-forbidden-write'); ROLLBACK;", check=False)
        assert denied.returncode != 0 and "row-level security" in denied.stderr
        print(json.dumps({"result": "PASS", "migrations": len(migrations),
                          "matching_columns": len(actual), "rls_tables": 8,
                          "realtime_publication": "draws", "anonymous_write_denied": True,
                          "matching_structure": structure_counts,
                          "production_rows_copied": False, "network": "none"}), flush=True)
    finally:
        if created:
            run(["docker", "rm", "-f", name], timeout=30)


if __name__ == "__main__":
    main()
