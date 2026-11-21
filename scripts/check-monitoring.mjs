// Integration test: real Node HTTP requests through the production preload.
import assert from "node:assert/strict";
import { once } from "node:events";
import { createServer } from "node:http";
import { createRequire } from "node:module";

createRequire(import.meta.url)("./http-metrics.cjs");
const store = globalThis[Symbol.for("koipond.http.metrics")];
assert(store && store.version === 1);
const server = createServer((request, response) => {
  const path = request.url.split("?", 1)[0];
  if (path.startsWith("/api/admin")) {
    response.writeHead(403).end();
  } else if (path === "/api/eventsub" || path === "/api/activity") {
    response.writeHead(204).end();
  } else if (path.startsWith("/api/auth")) {
    response.writeHead(302, { Location: "/" }).end();
  } else if (path.startsWith("/overlay/")) {
    setTimeout(() => response.writeHead(200).end("overlay"), 30);
  } else if (path.startsWith("/unmapped") || path.startsWith("/overlayXYZ")) {
    response.writeHead(404).end();
  } else {
    response.writeHead(200).end("ok");
  }
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const base = `http://127.0.0.1:${server.address().port}`;
try {
  for (const [path, method] of [
    ["/overlay/private-channel-name?code=secret-oauth-value", "GET"],
    ["/api/admin/entries?filter=private-filter", "POST"],
    ["/health/live", "GET"], ["/api/internal/metrics", "GET"],
    ["/api/activity", "POST"], ["/api/eventsub", "POST"],
    ["/unmapped/private-path", "GET"], ["/overlayXYZ/private-name", "GET"],
    ["/admin?token=private-session", "GET"], ["/_next/static/chunk.js", "GET"],
    ["/api/auth/twitch/callback?code=private-oauth", "GET"],
  ]) {
    const response = await fetch(base + path, { method, redirect: "manual" });
    await response.arrayBuffer();
  }
  const samples = [...store.requests.values()];
  assert.equal(samples.reduce((total, sample) => total + sample.count, 0), 8);
  assert.equal(samples.find((sample) => sample.route === "other").count, 2);
  assert.equal(samples.find((sample) => sample.route === "/api/admin").status, "403");
  assert.equal(samples.find((sample) => sample.route === "/api/auth").status, "302");
  assert.equal(samples.find((sample) => sample.route === "/api/eventsub").method, "POST");
  const overlay = samples.find((sample) => sample.route === "/overlay");
  assert(overlay.sum >= 0.025, "Histogram must measure elapsed request work");
  assert.equal(overlay.buckets[0], 0);
  for (const sample of samples) {
    assert.equal(sample.buckets.at(-1), sample.count);
    for (let i = 1; i < sample.buckets.length; i++) assert(sample.buckets[i] >= sample.buckets[i - 1]);
    assert(sample.sum >= 0 && Number.isFinite(sample.sum));
  }
  const metadata = JSON.stringify(samples);
  for (const sensitive of ["private-channel", "secret-oauth", "private-filter", "private-session", "private-oauth", "private-path", "private-name"]) {
    assert(!metadata.includes(sensitive), "Metrics must not retain identifiers or query strings");
  }
  assert(!samples.some((sample) => sample.route.startsWith("/health") || sample.route.startsWith("/api/internal") || sample.route === "/api/activity"));
  console.log(JSON.stringify({ result: "PASS", completedRequests: 8, fixedRouteGroups: samples.length, operationalRequestsExcluded: 3, identifiersRetained: false }));
} finally {
  server.closeAllConnections();
  await new Promise((done) => server.close(done));
}
