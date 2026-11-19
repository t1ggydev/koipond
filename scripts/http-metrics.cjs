"use strict";

// Loaded once before the HTTP server. Records completed requests only; never
// retains raw URLs, query strings, headers, account names or response bodies.
// eslint-disable-next-line @typescript-eslint/no-require-imports -- Node --require preload is CommonJS.
const http = require("node:http");
const key = Symbol.for("koipond.http.metrics");
if (!globalThis[key]) {
  const boundaries = [.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10];
  const store = { version: 1, boundaries, requests: new Map() };
  globalThis[key] = store;
  const methods = new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]);
  const under = (path, prefix) => path === prefix || path.startsWith(prefix + "/");
  function routeGroup(raw) {
    const path = typeof raw === "string" ? raw.split("?", 1)[0] : "";
    if (under(path, "/health") || under(path, "/api/internal") || under(path, "/api/activity")) return null;
    if (path === "/") return "/";
    if (under(path, "/_next") || under(path, "/static") || path === "/favicon.ico") return "/static";
    for (const prefix of ["/api/admin", "/api/auth", "/api/eventsub", "/admin", "/overlay", "/demo"]) {
      if (under(path, prefix)) return prefix;
    }
    return "other";
  }
  const originalEmit = http.Server.prototype.emit;
  http.Server.prototype.emit = function emit(event, request, response) {
    if (event === "request" && request && response) {
      const route = routeGroup(request.url);
      if (route !== null) {
        const method = methods.has(request.method) ? request.method : "OTHER";
        const started = process.hrtime.bigint();
        response.once("finish", () => {
          const elapsed = Number(process.hrtime.bigint() - started) / 1e9;
          const status = Number.isInteger(response.statusCode) && response.statusCode >= 100 && response.statusCode <= 599
            ? String(response.statusCode) : "500";
          const identity = `${method}|${route}|${status}`;
          let sample = store.requests.get(identity);
          if (!sample) {
            sample = { method, route, status, count: 0, sum: 0, buckets: boundaries.map(() => 0) };
            store.requests.set(identity, sample);
          }
          sample.count++;
          sample.sum += elapsed;
          boundaries.forEach((boundary, index) => { if (elapsed <= boundary) sample.buckets[index]++; });
        });
      }
    }
    return originalEmit.apply(this, arguments);
  };
}
