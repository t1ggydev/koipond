// Disposable integration gateway: real PostgREST, synthetic Realtime broadcast.
import http from "node:http";
if (process.env.KOIPOND_ISOLATED_TEST !== "1") throw new Error("Test gateway cannot run outside isolated tests");
http.createServer((request, response) => {
  if (request.url === "/health") { response.writeHead(200).end("ok"); return; }
  if (request.url === "/realtime/v1/api/broadcast" && request.method === "POST") {
    request.resume();
    response.writeHead(200, { "Content-Type": "application/json" }).end('{"accepted":true}');
    return;
  }
  if (!request.url?.startsWith("/rest/v1")) { response.writeHead(404).end(); return; }
  const upstream = http.request({
    host: "postgrest", port: 3000, method: request.method,
    path: request.url.slice("/rest/v1".length) || "/",
    headers: { ...request.headers, host: "postgrest:3000" }, timeout: 7000,
  }, (result) => {
    response.writeHead(result.statusCode ?? 503, result.headers);
    result.pipe(response);
  });
  upstream.on("timeout", () => upstream.destroy());
  upstream.on("error", () => { if (!response.headersSent) response.writeHead(503); response.end(); });
  request.pipe(upstream);
}).listen(8000, "0.0.0.0");
