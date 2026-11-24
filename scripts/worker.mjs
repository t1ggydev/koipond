// A separate, bounded worker runner. No Twitch credentials are needed here.
const token = process.env.KOIPOND_WORKER_TOKEN;
const urls = (process.env.KOIPOND_WORKER_URLS ?? "http://web:3000/api/internal/worker/run")
  .split(",").map((value) => value.trim()).filter(Boolean);
if (!token || token.length < 32 || urls.length === 0) {
  throw new Error("Worker requires a private endpoint and independent worker token");
}
for (const url of urls) {
  const parsed = new URL(url);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password ||
      parsed.pathname !== "/api/internal/worker/run") throw new Error("Invalid private worker endpoint");
}
const lifetime = new AbortController();
for (const signal of ["SIGINT", "SIGTERM"]) process.once(signal, () => lifetime.abort());
let index = 0;
while (!lifetime.signal.aborted) {
  let wait = 250;
  try {
    const response = await fetch(urls[index++ % urls.length], {
      method: "POST", headers: { Authorization: `Bearer ${token}` }, redirect: "error",
      signal: AbortSignal.any([lifetime.signal, AbortSignal.timeout(55_000)]),
    });
    if (response.ok) {
      const result = await response.json();
      wait = result.processed ? 0 : 250;
    } else {
      console.error("Worker request failed", response.status);
      wait = 2000;
    }
  } catch {
    if (!lifetime.signal.aborted) console.error("Worker endpoint unavailable");
    wait = 2000;
  }
  if (!lifetime.signal.aborted) await new Promise((done) => setTimeout(done, wait));
}
