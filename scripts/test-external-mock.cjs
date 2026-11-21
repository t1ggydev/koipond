"use strict";
// Test harness only: never copied into the production runner image.
if (process.env.KOIPOND_ISOLATED_TEST !== "1") throw new Error("External mock requires isolated test mode");
const realFetch = globalThis.fetch;
globalThis.fetch = async function isolatedFetch(input, init) {
  const url = new URL(typeof input === "string" || input instanceof URL ? input : input.url);
  if (init?.signal?.aborted) throw init.signal.reason;
  if (url.hostname === "id.twitch.tv" && url.pathname === "/oauth2/token") {
    return Response.json({ access_token: "synthetic-integration-app-token", expires_in: 3600 });
  }
  if (url.hostname === "api.twitch.tv" && url.pathname === "/helix/chat/messages") {
    return Response.json({ data: [{ message_id: "synthetic-chat-message", is_sent: true }] });
  }
  if (["gateway", "postgrest", "web1", "web2", "127.0.0.1", "localhost"].includes(url.hostname)) {
    return realFetch(input, init);
  }
  throw new Error("External URL blocked by isolated integration harness");
};
