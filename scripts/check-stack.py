#!/usr/bin/env python3
"""Disposable two-instance integration stack; no production endpoints or keys."""
import argparse
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
POSTGRES = "postgres:17-bookworm@sha256:837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b"
POSTGREST = "postgrest/postgrest:v16.6@sha256:5ce030c153500fe90ba157da4da8ba99ab51d3bc7b95e071bd0b544b67a632f7"


def run(argv, **kwargs):
    result = subprocess.run(argv, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError("Isolated command failed: " + argv[0] + " " + result.stderr[-1500:])
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="koipond-prep:operational")
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    assert args.image.startswith("koipond-prep:"), "Never overwrite a production image"
    if not args.skip_build:
        run(["docker", "build", "--tag", args.image, "--label", "koipond.stage=preparation",
             "--build-arg", "NEXT_PUBLIC_APP_URL=http://koipond.test",
             "--build-arg", "NEXT_PUBLIC_SUPABASE_URL=http://gateway:8000",
             "--build-arg", "NEXT_PUBLIC_SUPABASE_ANON_KEY=isolated-build-placeholder", str(ROOT)], timeout=600)
    suffix = uuid.uuid4().hex[:10]
    network = "koipond-check-" + suffix
    names = []
    created_network = False
    jwt_secret = secrets.token_hex(32)
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    signed = encode({"alg": "HS256", "typ": "JWT"}) + "." + encode({
        "role": "service_role", "ref": "isolated-koipond", "exp": int(time.time()) + 3600,
    })
    service_key = signed + "." + base64.urlsafe_b64encode(hmac.new(jwt_secret.encode(), signed.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    shared = {
        "KOIPOND_ISOLATED_TEST": "1", "KOIPOND_DURABLE_EVENTS": "true",
        "NEXT_PUBLIC_APP_URL": "http://koipond.test", "SUPABASE_INTERNAL_URL": "http://gateway:8000",
        "NEXT_PUBLIC_SUPABASE_URL": "http://gateway:8000", "SUPABASE_SERVICE_ROLE_KEY": service_key,
        "SESSION_SECRET": secrets.token_hex(32), "TWITCH_EVENTSUB_SECRET": secrets.token_hex(32),
        "KOIPOND_WORKER_TOKEN": secrets.token_hex(32), "KOIPOND_MONITORING_TOKEN": secrets.token_hex(32),
        "KOIPOND_METRICS_PSEUDONYM_SECRET": secrets.token_hex(32),
        "NEXT_SERVER_ACTIONS_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "TWITCH_CLIENT_ID": "isolated-test", "TWITCH_CLIENT_SECRET": "isolated-test",
        "TWITCH_BOT_USER_ID": "999", "ENABLE_DEMO": "false", "CRON_SECRET": secrets.token_hex(32),
    }

    def start(alias, image, command=(), env=None, mounts=(), extra=(), memory="768m"):
        name = "koipond-check-" + suffix + "-" + alias
        names.append(name)
        argv = ["docker", "run", "-d", "--rm", "--name", name, "--network", network,
                "--network-alias", alias, "--memory", memory, "--cpus", "1", "--log-driver", "none"]
        for field, value in (env or {}).items():
            argv += ["-e", field + "=" + value]
        for source, target in mounts:
            argv += ["--mount", "type=bind,source=" + str(source) + ",target=" + target + ",readonly"]
        run(argv + list(extra) + [image] + list(command), timeout=180)
        return name

    try:
        run(["docker", "network", "create", "--internal", network])
        created_network = True
        database = start("db", POSTGRES, ["-c", "wal_level=logical"],
                         {"POSTGRES_HOST_AUTH_METHOD": "trust"},
                         extra=["--tmpfs", "/var/lib/postgresql/data:rw,noexec,nosuid,size=256m"])
        for _ in range(90):
            ready = subprocess.run(["docker", "exec", database, "pg_isready", "-U", "postgres"], capture_output=True)
            process = run(["docker", "exec", database, "cat", "/proc/1/comm"])
            if ready.returncode == 0 and process.strip() == "postgres":
                break
            time.sleep(1)
        else:
            raise RuntimeError("Isolated database startup timed out")
        psql = ["docker", "exec", "-i", database, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres"]
        run(psql, input="CREATE ROLE anon NOLOGIN; CREATE ROLE authenticated NOLOGIN; CREATE ROLE service_role NOLOGIN BYPASSRLS; CREATE PUBLICATION supabase_realtime;")
        migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
        assert len(migrations) == 18
        for migration in migrations:
            run(psql, input="BEGIN;\n" + migration.read_text() + "\nCOMMIT;")
        start("postgrest", POSTGREST, env={
            "PGRST_DB_URI": "postgres://postgres@db:5432/postgres",
            "PGRST_DB_SCHEMAS": "public", "PGRST_DB_ANON_ROLE": "anon",
            "PGRST_JWT_SECRET": jwt_secret, "PGRST_SERVER_PORT": "3000", "PGRST_LOG_LEVEL": "error",
        }, memory="256m")
        start("gateway", args.image, ["node", "/tmp/test-gateway.mjs"], shared,
              [(ROOT / "scripts/test-gateway.mjs", "/tmp/test-gateway.mjs")], memory="128m")
        for alias in ["web1", "web2"]:
            start(alias, args.image, ["node", "--require", "/app/observability.cjs", "--require",
                  "/tmp/test-external-mock.cjs", "/app/server.js"], shared,
                  [(ROOT / "scripts/test-external-mock.cjs", "/tmp/test-external-mock.cjs")])
        test_env = {**shared, "KOIPOND_TEST_WEB_URLS": "http://web1:3000,http://web2:3000",
                    "KOIPOND_TEST_API_URL": "http://gateway:8000", "KOIPOND_TEST_SERVICE_KEY": service_key,
                    "KOIPOND_WORKER_URLS": "http://web1:3000/api/internal/worker/run,http://web2:3000/api/internal/worker/run"}
        # Run foreground so test stdout is captured without persisting container logs.
        tester = "koipond-check-" + suffix + "-tests"
        names.append(tester)
        argv = ["docker", "run", "--rm", "--name", tester, "--network", network,
                "--memory", "768m", "--cpus", "1", "--log-driver", "none"]
        for field, value in test_env.items():
            argv += ["-e", field + "=" + value]
        argv += ["--mount", "type=bind,source=" + str(ROOT / "scripts/check-multi-instance.mjs") + ",target=/tmp/check-multi-instance.mjs,readonly",
                 args.image, "node", "/tmp/check-multi-instance.mjs"]
        # Wait for both Node processes and the actual REST schema cache.
        probe = "Promise.all(['http://web1:3000/health/live','http://web2:3000/health/live','http://gateway:8000/rest/v1/participants?select=id&limit=1'].map(async u=>{const r=await fetch(u,{headers:{Authorization:'Bearer '+process.env.SUPABASE_SERVICE_ROLE_KEY},signal:AbortSignal.timeout(1000)});if(!r.ok)throw Error('not ready')})).catch(()=>process.exit(1))"
        for _ in range(60):
            result = subprocess.run(["docker", "exec", names[-2], "node", "-e", probe], capture_output=True)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Private integration stack readiness timed out")
        output = run(argv, timeout=240)
        print(output.strip(), flush=True)
        print(json.dumps({"cleanup": "scheduled", "production_connections": False,
                          "private_network": "internal", "published_ports": 0}), flush=True)
    finally:
        for name in reversed(names):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        if created_network:
            run(["docker", "network", "rm", network], timeout=30)


if __name__ == "__main__":
    main()
