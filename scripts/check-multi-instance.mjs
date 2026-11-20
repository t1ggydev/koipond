// Destructive fixture writes are restricted to isolated Docker/.test endpoints.
// Requires two running web processes, PostgREST and migration 0017. No real
// Twitch/OAuth requests may only reach the parent-provided test preload mock.
import assert from "node:assert/strict";
import { createHmac, randomInt, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

function required(name, fallback) {
  const value = process.env[name] || (fallback && process.env[fallback]);
  assert(value, `Missing isolated integration test setting ${name}`);
  return value;
}

function isolatedUrl(value) {
  const url = new URL(value);
  const hosts = new Set(["web1", "web2", "gateway", "postgrest", "localhost", "127.0.0.1"]);
  assert(url.protocol === "http:" || url.protocol === "https:", "Unexpected test URL protocol");
  assert(hosts.has(url.hostname) || url.hostname.endsWith(".test"), "Only isolated Docker/.test hosts are allowed");
  assert(!url.username && !url.password && !url.search && !url.hash, "Do not put credentials or queries in test base URLs");
  return url.origin;
}

const webs = required("KOIPOND_TEST_WEB_URLS").split(",").map((url) => isolatedUrl(url.trim()));
assert.equal(webs.length, 2, "Exactly two isolated web instances are required");
assert.notEqual(webs[0], webs[1], "Web instance URLs must differ");
const api = isolatedUrl(required("KOIPOND_TEST_API_URL"));
const appOrigin = isolatedUrl(required("NEXT_PUBLIC_APP_URL"));
assert.equal(process.env.KOIPOND_ISOLATED_TEST, "1", "Enable commands only under the confirmed external-request-blocking test preload");
const serviceKey = required("KOIPOND_TEST_SERVICE_KEY");
try {
  const payload = JSON.parse(Buffer.from(serviceKey.split(".")[1], "base64url").toString("utf8"));
  assert.equal(payload.role, "service_role", "Use the synthetic service-role JWT");
  assert.notEqual(payload.ref, "xlatvbaiwfftkxgtzrly", "Production Supabase JWTs must never be used for tests");
} catch {
  throw new Error("A valid synthetic service-role JWT is required");
}
const sessionSecret = required("SESSION_SECRET");
const eventSecret = required("TWITCH_EVENTSUB_SECRET");
const workerToken = required("KOIPOND_TEST_WORKER_TOKEN", "KOIPOND_WORKER_TOKEN");
const monitoringToken = required("KOIPOND_TEST_MONITORING_TOKEN", "KOIPOND_MONITORING_TOKEN");
const streamerId = "a1000000-0000-4000-8000-000000000001";
const participantId = "a2000000-0000-4000-8000-000000000001";
const settingsId = "a3000000-0000-4000-8000-000000000001";
const session = randomInt(1000, 1_000_000_000);
const messageId = `isolated-multi-join-${randomUUID()}`;

async function request(url, options = {}) {
  return fetch(url, {
    ...options,
    redirect: "manual",
    signal: AbortSignal.timeout(10_000),
  });
}

async function rest(path, options = {}) {
  const response = await request(`${api}/rest/v1/${path}`, {
    ...options,
    headers: {
      apikey: serviceKey,
      Authorization: `Bearer ${serviceKey}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  assert(response.ok, `Isolated REST ${options.method || "GET"} ${path.split("?", 1)[0]} returned ${response.status}`);
  if (response.status === 204 || response.headers.get("content-length") === "0") return null;
  const body = await response.text();
  return body ? JSON.parse(body) : null;
}

async function seed() {
  const preferences = { Prefer: "resolution=merge-duplicates,return=minimal" };
  await rest("streamers?on_conflict=id", {
    method: "POST", headers: preferences,
    body: JSON.stringify({ id: streamerId, twitch_id: "100", twitch_login: "teststreamer", twitch_display_name: "Test", bot_scope_granted: false }),
  });
  await rest("participants?on_conflict=id", {
    method: "POST", headers: preferences,
    body: JSON.stringify({ id: participantId, twitch_id: "200", twitch_login: "testviewer", twitch_display_name: "Viewer", osu_id: 200, osu_username: "TestViewer" }),
  });
  await rest("participants?on_conflict=id", {
    method: "POST", headers: preferences,
    body: JSON.stringify({ id: "a2000000-0000-4000-8000-000000000002", twitch_id: "300", twitch_login: "secondviewer", twitch_display_name: "Second viewer", osu_id: 300, osu_username: "SecondTestViewer" }),
  });
  await rest("settings?on_conflict=streamer_id", {
    method: "POST", headers: preferences,
    body: JSON.stringify({
      id: settingsId, streamer_id: streamerId,
      trigger_word: "!join", entries_open: true, entries_session: session,
      draw_mode: "keyword", chat_announcement: false, ignore_osu_criteria: true,
      subscribers_only: false, unique_winners: true,
      chat_capture_draw_id: null, chat_capture_twitch_id: null, chat_capture_until: null,
    }),
  });
}

function signedSession() {
  const value = Buffer.from(JSON.stringify({
    twitchId: "100", twitchLogin: "teststreamer", twitchDisplayName: "Test", exp: Date.now() + 3_600_000,
  })).toString("base64url");
  const signature = createHmac("sha256", sessionSecret).update(value).digest("base64url");
  return `koi_session=${value}.${signature}`;
}

const cookie = signedSession();
function chatBody(id, text, user = "200", badges = []) {
  return JSON.stringify({
    subscription: { type: "channel.chat.message" },
    event: {
      broadcaster_user_id: "100", chatter_user_id: user,
      chatter_user_login: user === "100" ? "teststreamer" : `testviewer${user}`,
      chatter_user_name: user === "100" ? "Test" : `Viewer ${user}`, message_id: id,
      message: { text, fragments: [{ type: "text", text }] }, badges,
    },
  });
}
const raw = chatBody(messageId, "!join");

async function event(web, { id = messageId, body = raw, timestamp = new Date().toISOString(), corrupt = false } = {}) {
  const signature = createHmac("sha256", eventSecret).update(id + timestamp + body).digest("hex");
  return request(`${web}/api/eventsub`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Twitch-Eventsub-Message-Id": id,
      "Twitch-Eventsub-Message-Timestamp": timestamp,
      "Twitch-Eventsub-Message-Signature": `sha256=${corrupt ? "0".repeat(64) : signature}`,
      "Twitch-Eventsub-Message-Type": "notification",
    },
    body,
  });
}

async function metrics(web) {
  const response = await request(`${web}/api/internal/metrics`, { headers: { Authorization: `Bearer ${monitoringToken}` } });
  assert.equal(response.status, 200, "Operational metrics must be available");
  const text = await response.text();
  const scalar = (name) => {
    const line = text.split("\n").find((candidate) => candidate.startsWith(`${name} `));
    assert(line, `Missing metric ${name}`);
    const value = Number(line.slice(name.length + 1));
    assert(Number.isFinite(value), `Invalid metric ${name}`);
    return value;
  };
  assert.equal(scalar("koipond_http_instrumentation_enabled"), 1, "Both instances must load HTTP instrumentation");
  return { text, scalar };
}

async function worker(web) {
  const response = await request(`${web}/api/internal/worker/run`, {
    method: "POST", headers: { Authorization: `Bearer ${workerToken}` },
  });
  assert.equal(response.status, 200, "Isolated worker invocation failed");
  const body = await response.json();
  assert.equal(typeof body.processed, "boolean", "Worker response must identify whether a job was processed");
  return body;
}

async function entries() {
  return rest(`entries?streamer_id=eq.${streamerId}&session=eq.${session}&select=twitch_id`);
}

async function drainQueue() {
  for (let attempt = 0; attempt < 20; attempt++) {
    const data = await metrics(webs[attempt % webs.length]);
    if (data.scalar("koipond_queue_pending") === 0 && data.scalar("koipond_queue_processing") === 0) return;
    await Promise.all(webs.map((web) => worker(web)));
    await new Promise((done) => setTimeout(done, 100));
  }
  throw new Error("Isolated queue did not drain within the bounded test attempts");
}

let checks = 0;
await seed();
for (const web of webs) {
  const response = await request(`${web}/api/admin/entries`, { headers: { Cookie: cookie } });
  assert.equal(response.status, 200, "The same signed session must work on both instances");
  assert.equal((await response.json()).entriesOpen, true);
  checks++;
  assert.equal((await request(`${web}/api/internal/metrics`, { headers: { Authorization: "Bearer invalid-test-token" } })).status, 401);
  checks++;
}

const before = await metrics(webs[0]);
const duplicateBefore = before.scalar("koipond_events_duplicate_total");
assert.equal((await entries()).length, 0);
const duplicateResponses = await Promise.all(webs.map((web) => event(web)));
for (const response of duplicateResponses) assert.equal(response.status, 204);
checks++;

const workers = await Promise.all(webs.map((web) => worker(web)));
assert.equal(workers.filter((result) => result.processed).length, 1, "Exactly one worker must claim the single queued event");
let inserted = await entries();
for (let attempt = 0; inserted.length !== 1 && attempt < 10; attempt++) {
  await new Promise((done) => setTimeout(done, 100));
  await worker(webs[attempt % webs.length]);
  inserted = await entries();
}
assert.equal(inserted.length, 1, "The event must create exactly one entry");
assert.equal(inserted[0].twitch_id, "200");
checks++;

assert.equal((await event(webs[1])).status, 204);
const afterReplay = await worker(webs[0]);
assert.equal(afterReplay.processed, false, "A processed duplicate must not become a fresh job");
assert.equal((await entries()).length, 1);
checks++;

// Broadcaster command uses the same HTTP entry point and real durable worker.
// Parent must intercept and block all non-isolated outbound requests.
const commandId = `isolated-command-${randomUUID()}`;
const commandBody = chatBody(commandId, "!pickwinner", "100", [{ set_id: "broadcaster" }]);
assert.equal((await event(webs[0], { id: commandId, body: commandBody })).status, 204);
await drainQueue();
const commandDraws = () => rest(`draws?source_event_id=eq.${commandId}&select=id,winner_twitch_id`);
assert.equal((await commandDraws()).length, 1, "A command must commit one draw");
assert.equal((await commandDraws())[0].winner_twitch_id, "200");
checks++;
for (const response of await Promise.all(webs.map((web) => event(web, { id: commandId, body: commandBody })))) {
  assert.equal(response.status, 204);
}
await drainQueue();
assert.equal((await commandDraws()).length, 1, "Replayed commands must never commit a second draw");
checks++;

const numberSession = session + 5;
await rest(`settings?streamer_id=eq.${streamerId}`, {
  method: "PATCH", headers: { Prefer: "return=minimal" },
  body: JSON.stringify({
    entries_open: true, entries_session: numberSession, draw_mode: "number",
    number_min: 1, number_max: 10, number_target: 7, number_drawn_at: new Date().toISOString(),
    chat_announcement: false, chat_capture_draw_id: null, chat_capture_twitch_id: null, chat_capture_until: null,
  }),
});
const numberIds = [`isolated-number-${randomUUID()}`, `isolated-number-${randomUUID()}`];
for (const response of await Promise.all(webs.map((web, index) => event(web, {
  id: numberIds[index], body: chatBody(numberIds[index], "7", index === 0 ? "200" : "300"),
})))) assert.equal(response.status, 204);
await Promise.all(webs.map((web) => worker(web)));
await drainQueue();
const numberDraws = await rest(`draws?streamer_id=eq.${streamerId}&session=eq.${numberSession}&select=id,source_event_id`);
assert.equal(numberDraws.length, 1, "Competing correct guesses must create exactly one draw");
assert(numberIds.includes(numberDraws[0].source_event_id));
const closedSettings = await rest(`settings?streamer_id=eq.${streamerId}&select=entries_open,entries_session,number_target`);
assert.equal(closedSettings[0].entries_open, false);
assert.equal(closedSettings[0].entries_session, numberSession + 1);
assert.equal(closedSettings[0].number_target, null);
checks++;

assert.equal((await request(`${webs[0]}/api/activity`, { method: "POST", headers: { Origin: appOrigin } })).status, 401);
assert.equal((await request(`${webs[0]}/api/activity`, { method: "POST", headers: { Origin: "http://wrong.test", Cookie: cookie } })).status, 403);
checks += 2;
for (const web of webs) {
  const response = await request(`${web}/api/activity`, { method: "POST", headers: { Origin: appOrigin, Cookie: cookie } });
  assert.equal(response.status, 204, "Activity requires a shared metrics pseudonym secret and migration 0017");
}
for (const web of webs) {
  const data = await metrics(web);
  assert.equal(data.scalar("koipond_active_users"), 1, "The same user must not be counted twice across instances");
  assert.equal(data.scalar("koipond_active_streamers"), 1);
  assert(data.scalar("koipond_events_duplicate_total") >= duplicateBefore + 2);
  assert.equal(data.scalar("koipond_queue_pending"), 0);
  assert.equal(data.scalar("koipond_queue_processing"), 0);
  assert.match(data.text, /koipond_http_request_duration_seconds_bucket\{/);
  checks++;
}

assert.equal((await event(webs[0], { id: `bad-${randomUUID()}`, corrupt: true })).status, 403);
assert.equal((await event(webs[1], { id: `malformed-${randomUUID()}`, body: "{\"broken\":" })).status, 400);
assert.equal((await event(webs[0], { id: `old-${randomUUID()}`, timestamp: new Date(Date.now() - 11 * 60_000).toISOString() })).status, 403);
checks += 3;
await metrics(webs[1]);

// A message received while capture is open must survive queue delay beyond the
// window. The recorded timestamp is the database receipt time, not worker time.
const captureDraw = (await commandDraws())[0];
const captureUntil = new Date(Date.now() + 1500).toISOString();
await rest(`settings?streamer_id=eq.${streamerId}`, {
  method: "PATCH", headers: { Prefer: "return=minimal" },
  body: JSON.stringify({
    chat_capture_draw_id: captureDraw.id, chat_capture_twitch_id: "200", chat_capture_until: captureUntil,
  }),
});
const captureMessageId = `isolated-delayed-capture-${randomUUID()}`;
assert.equal((await event(webs[0], {
  id: captureMessageId, body: chatBody(captureMessageId, "isolated capture window message", "200"),
})).status, 204);
await new Promise((done) => setTimeout(done, 1800));
assert(Date.now() > Date.parse(captureUntil), "Capture window must have expired before worker processing");
await drainQueue();
const captured = await rest(`winner_messages?source_event_id=eq.${captureMessageId}&select=draw_id,twitch_id,sent_at`);
assert.equal(captured.length, 1, "Queue delay must not lose a message received within the capture window");
assert.equal(captured[0].draw_id, captureDraw.id);
assert.equal(captured[0].twitch_id, "200");
assert(Date.parse(captured[0].sent_at) < Date.parse(captureUntil), "Capture must retain its receipt timestamp");
checks++;

// Only now start a real runner process: earlier checks intentionally control
// worker invocation themselves to assert exact concurrent claim counts.
const runnerPath = process.env.KOIPOND_TEST_WORKER_SCRIPT || resolve("/app/worker.mjs");
assert(existsSync(runnerPath), "The built runtime image must contain worker.mjs");
const runnerSession = numberSession + 10;
await rest(`settings?streamer_id=eq.${streamerId}`, {
  method: "PATCH", headers: { Prefer: "return=minimal" },
  body: JSON.stringify({
    entries_open: true, entries_session: runnerSession, draw_mode: "keyword",
    number_target: null, number_drawn_at: null,
    chat_capture_draw_id: null, chat_capture_twitch_id: null, chat_capture_until: null,
  }),
});
const runner = spawn(process.execPath, [runnerPath], {
  env: {
    ...process.env,
    KOIPOND_WORKER_URLS: webs.map((web) => `${web}/api/internal/worker/run`).join(","),
    KOIPOND_WORKER_TOKEN: workerToken,
  },
  stdio: ["ignore", "ignore", "ignore"],
});
const exited = once(runner, "exit");
try {
  await once(runner, "spawn");
  const runnerMessageId = `isolated-runner-${randomUUID()}`;
  assert.equal((await event(webs[1], { id: runnerMessageId, body: chatBody(runnerMessageId, "!join", "300") })).status, 204);
  let processed = false;
  for (let attempt = 0; attempt < 80; attempt++) {
    assert.equal(runner.exitCode, null, "Worker runner must stay alive while polling");
    const rows = await rest(`entries?streamer_id=eq.${streamerId}&session=eq.${runnerSession}&select=twitch_id`);
    if (rows.length === 1) {
      assert.equal(rows[0].twitch_id, "300");
      const status = await metrics(webs[0]);
      if (status.scalar("koipond_queue_pending") === 0 && status.scalar("koipond_queue_processing") === 0) {
        processed = true;
        break;
      }
    }
    await new Promise((done) => setTimeout(done, 150));
  }
  assert(processed, "The real runner must process the queued event within bounded polling attempts");
  checks++;
} finally {
  runner.kill("SIGTERM");
  let timer;
  const outcome = await Promise.race([
    exited.then(([code, signal]) => ({ code, signal })),
    new Promise((done) => { timer = setTimeout(() => done(null), 5000); }),
  ]);
  clearTimeout(timer);
  if (!outcome) {
    runner.kill("SIGKILL");
    await exited;
    throw new Error("Worker runner did not terminate gracefully after SIGTERM");
  }
  assert.equal(outcome.code, 0, "Worker runner must handle SIGTERM and exit successfully");
  assert.equal(outcome.signal, null);
}
for (const web of webs) {
  const current = await metrics(web);
  assert.equal(current.scalar("koipond_http_instrumentation_enabled"), 1);
  assert.equal((await request(`${web}/api/admin/entries`, { headers: { Cookie: cookie } })).status, 200);
  checks++;
}
console.log(JSON.stringify({
  result: "PASS", checks, webInstances: 2, completedEntries: 1,
  sharedSession: true, deduplicatedPresence: true, productionEndpointsAllowed: false,
  commandDraws: 1, replayedCommandDraws: 1, competingNumberGuessDraws: 1,
  externalRequests: "parent test preload mock only",
  runnerProcessedEntry: true, runnerGracefulShutdown: true,
  delayedCapturePreserved: true,
  fixtureData: "synthetic only; isolated environment teardown removes it",
}));
