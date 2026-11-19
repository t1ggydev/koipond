// Run explicitly inside a private staging web container. Never targets hosted Supabase.
import { createRequire } from "node:module";
import { randomUUID } from "node:crypto";
const require = createRequire("/app/package.json");
// Next bundles its server dependencies; the probe may mount the matching public SDK bundle.
const { createClient } = require(process.env.KOIPOND_PROBE_CLIENT || "@supabase/supabase-js");
const env = process.env;
if (env.KOIPOND_PREPARATION_SMOKE !== "true" ||
    env.NEXT_PUBLIC_APP_URL !== "https://koipond-stage.kluge-it.tech" ||
    env.SUPABASE_INTERNAL_URL !== "http://10.10.10.30:8000" ||
    env.KOIPOND_DURABLE_EVENTS !== "true") throw new Error("Private staging guard failed");
const options = { auth: { persistSession: false, autoRefreshToken: false } };
const admin = createClient(env.SUPABASE_INTERNAL_URL, env.SUPABASE_SERVICE_ROLE_KEY, options);
const browser = createClient(env.SUPABASE_INTERNAL_URL, env.NEXT_PUBLIC_SUPABASE_ANON_KEY, options);
const checks = [];
function assert(value, name) { if (!value) throw new Error(name); checks.push(name); }
const probeId = randomUUID();
const marker = `koipond-preparation:${probeId}`;
let fixtureInserted = false;
let broadcastChannel;
let changesChannel;
const timers = new Set();
function deferred(name) {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  promise.catch(() => {}); // A later subscription may fail before this waiter is awaited.
  const timer = setTimeout(() => reject(new Error(name)), 20_000);
  timers.add(timer);
  return { promise, resolve: (value) => { clearTimeout(timer); resolve(value); }, reject };
}
try {
  const count = await admin.from("participants").select("id", { count: "exact", head: true });
  assert(!count.error && count.count >= 168, "restored_participants_readable");
  const denied = await browser.rpc("koi_operational_metrics");
  assert(Boolean(denied.error), "anonymous_operational_rpc_denied");
  const metrics = await admin.rpc("koi_operational_metrics");
  assert(!metrics.error && metrics.data !== null, "service_role_operational_rpc");
  for (const host of ["web1", "web2"]) {
    for (const path of ["/health/live", "/health/ready", "/api/internal/metrics"]) {
      const response = await fetch(`http://${host}:3000${path}`, {
        headers: { Authorization: `Bearer ${env.KOIPOND_MONITORING_TOKEN}` },
        signal: AbortSignal.timeout(5000),
      });
      assert(response.ok, `${host}_${path}_ok`);
    }
    const deniedMetrics = await fetch(`http://${host}:3000/api/internal/metrics`);
    assert(deniedMetrics.status === 401, `${host}_metrics_protected`);
  }
  const broadcastJoined = deferred("broadcast_subscription_timeout");
  const received = deferred("broadcast_delivery_timeout");
  broadcastChannel = browser.channel(marker).on("broadcast", { event: "ping" }, (event) => {
    if (event.payload?.probe === probeId) received.resolve(true);
  }).subscribe((status) => {
    if (status === "SUBSCRIBED") broadcastJoined.resolve(true);
  });
  await broadcastJoined.promise;
  const broadcast = await fetch(`${env.SUPABASE_INTERNAL_URL}/realtime/v1/api/broadcast`, {
    method: "POST", headers: { "Content-Type": "application/json",
      apikey: env.SUPABASE_SERVICE_ROLE_KEY, Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}` },
    body: JSON.stringify({ messages: [{ topic: marker, event: "ping", payload: { probe: probeId } }] }),
    signal: AbortSignal.timeout(5000),
  });
  assert(broadcast.ok, "realtime_rest_broadcast_accepted");
  assert(await received.promise, "realtime_websocket_broadcast_received");
  const changesJoined = deferred("postgres_subscription_timeout");
  const changesReady = deferred("postgres_replication_ready_timeout");
  const changeReceived = deferred("postgres_change_timeout");
  changesChannel = browser.channel(`${marker}:changes`).on("system", {}, (event) => {
    if (event.extension === "postgres_changes" && event.status === "ok") changesReady.resolve(true);
  }).on("postgres_changes", {
    event: "INSERT", schema: "public", table: "draws", filter: `id=eq.${probeId}`,
  }, (event) => { if (event.new.id === probeId) changeReceived.resolve(true); }).subscribe((status) => {
    if (status === "SUBSCRIBED") changesJoined.resolve(true);
  });
  await changesJoined.promise;
  await changesReady.promise;
  // This unscoped synthetic draw cannot appear in a streamer's filtered overlay.
  const inserted = await admin.from("draws").insert({ id: probeId, triggered_by: "dashboard",
    source_event_id: marker, criteria: { preparation_probe: true } });
  assert(!inserted.error, "owned_realtime_fixture_inserted");
  fixtureInserted = true;
  assert(await changeReceived.promise, "postgres_change_received_by_anonymous_browser");
} finally {
  for (const timer of timers) clearTimeout(timer);
  if (fixtureInserted) {
    const removed = await admin.from("draws").delete().eq("id", probeId).eq("source_event_id", marker);
    if (removed.error) throw new Error("Owned preparation fixture cleanup failed");
    checks.push("owned_fixture_removed");
  }
  if (broadcastChannel) await browser.removeChannel(broadcastChannel);
  if (changesChannel) await browser.removeChannel(changesChannel);
  await browser.removeAllChannels();
}
console.log(JSON.stringify({ result: "PASS", checks: checks.length, names: checks,
  realSupabase: true, realWebsocket: true, productionConnections: false }));
