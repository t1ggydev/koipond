// Starts only a local standalone build and a fake database endpoint.
// Never supplies production credentials or contacts a real Supabase instance.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { createServer } from "node:http";
import { createServer as createPortReservation } from "node:net";
import { resolve } from "node:path";

const directory = resolve(process.argv[2] ?? ".next/standalone");
assert(existsSync(resolve(directory, "server.js")), "Build standalone output first");
const token = "isolated-runtime-check-token-not-for-deployment";
const key = "isolated-fake-database-key";
let calls = 0;
let mode = "healthy";
const upstream = createServer((request, response) => {
  calls++;
  if (request.url !== "/rest/v1/participants?select=id&limit=1" ||
      request.method !== "HEAD" || request.headers.apikey !== key ||
      request.headers.authorization !== `Bearer ${key}`) {
    response.writeHead(400).end();
    return;
  }
  if (mode === "slow") {
    setTimeout(() => response.writeHead(200).end(), 3500).unref();
  } else if (mode === "redirect") {
    response.writeHead(302, { Location: "/unexpected-secret-destination" }).end();
  } else {
    response.writeHead(mode === "healthy" ? 200 : 503).end();
  }
});
upstream.listen(0, "127.0.0.1");
await once(upstream, "listening");
const upstreamPort = upstream.address().port;

async function startApp(configured) {
  const reservation = createPortReservation();
  reservation.listen(0, "127.0.0.1");
  await once(reservation, "listening");
  const port = reservation.address().port;
  await new Promise((done) => reservation.close(done));
  const child = spawn(process.execPath, [resolve(directory, "server.js")], {
    cwd: directory,
    env: {
      ...process.env,
      NODE_ENV: "production",
      HOSTNAME: "127.0.0.1",
      PORT: String(port),
      ENABLE_DEMO: "false",
      SUPABASE_INTERNAL_URL: `http://127.0.0.1:${upstreamPort}`,
      SUPABASE_SERVICE_ROLE_KEY: key,
      KOIPOND_MONITORING_TOKEN: configured ? token : "",
      CRON_SECRET: "",
    },
    stdio: ["ignore", "ignore", "ignore"],
  });
  const base = `http://127.0.0.1:${port}`;
  const stop = async () => {
    if (child.exitCode !== null) return;
    const closed = once(child, "close");
    child.kill();
    await closed;
  };
  try {
    for (let i = 0; i < 60; i++) {
      assert(child.exitCode === null, "Standalone process exited during startup");
      try {
        const response = await fetch(`${base}/health/live`, { signal: AbortSignal.timeout(500) });
        if (response.ok) return { base, stop };
      } catch {}
      await new Promise((done) => setTimeout(done, 250));
    }
    throw new Error("Standalone startup timed out");
  } catch (error) {
    await stop();
    throw error;
  }
}

let app;
let checks = 0;
try {
  app = await startApp(true);
  const get = (path, authenticated = false) => fetch(`${app.base}${path}`, {
    headers: authenticated ? { Authorization: `Bearer ${token}` } : {},
    signal: AbortSignal.timeout(5000),
  });
  for (const path of ["/api/internal/metrics", "/health/ready"]) {
    assert.equal((await get(path)).status, 401);
    checks++;
  }
  assert.equal(calls, 0, "Unauthenticated requests must not probe the database");
  const metrics = await get("/api/internal/metrics", true);
  assert.equal(metrics.status, 200);
  assert.match(metrics.headers.get("content-type"), /text\/plain/);
  assert.equal(metrics.headers.get("cache-control"), "no-store");
  const body = await metrics.text();
  assert.match(body, /koipond_process_resident_memory_bytes [1-9][0-9]*/);
  assert.match(body, /# TYPE koipond_process_cpu_user_seconds_total counter/);
  assert(!body.includes(token) && !body.includes(key));
  checks++;

  const ready = await get("/health/ready", true);
  assert.equal(ready.status, 200);
  assert.equal((await ready.json()).checks.database, true);
  checks++;
  mode = "failed";
  assert.equal((await get("/health/ready", true)).status, 503);
  checks++;
  mode = "redirect";
  const beforeRedirect = calls;
  assert.equal((await get("/health/ready", true)).status, 503);
  assert.equal(calls, beforeRedirect + 1, "Never forward credentials across redirects");
  checks++;
  mode = "slow";
  const started = performance.now();
  assert.equal((await get("/health/ready", true)).status, 503);
  assert(performance.now() - started < 3000, "Database readiness must have a bounded timeout");
  checks++;
  assert.equal((await get("/api/admin/entries")).status, 403);
  assert.equal((await get("/api/cron/refresh-osu-stats")).status, 503);
  assert.equal((await get("/api/demo/state")).status, 404);
  checks += 3;
  await app.stop();
  app = await startApp(false);
  const beforeMissingToken = calls;
  for (const path of ["/api/internal/metrics", "/health/ready"]) {
    const response = await fetch(`${app.base}${path}`, { signal: AbortSignal.timeout(3000) });
    assert.equal(response.status, 503);
    checks++;
  }
  assert.equal(calls, beforeMissingToken, "Missing monitoring configuration must fail closed");
  console.log(JSON.stringify({ result: "PASS", checks, upstream: "local mock only", productionCredentials: false }));
} finally {
  if (app) await app.stop();
  upstream.closeAllConnections();
  await new Promise((done) => upstream.close(done));
}
