import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { env } from "@/lib/env";
import { isDuplicateMessage, isFreshMessage, verifyEventSubSignature } from "@/lib/eventsubVerify";
import { durableEventsEnabled, enqueueEvent } from "@/lib/operationalQueue";
import { processChatEvent } from "@/lib/eventsubProcessor";
import { getStreamerByTwitchId } from "@/lib/streamers";
import { getEntrySettings } from "@/lib/entries";

const chat = z.object({
  broadcaster_user_id: z.string().regex(/^\d{1,30}$/),
  chatter_user_id: z.string().regex(/^\d{1,30}$/),
  chatter_user_login: z.string().max(100), chatter_user_name: z.string().max(200),
  message_id: z.string().max(200).optional(), color: z.string().max(50).optional(),
  message: z.object({ text: z.string().max(10000), fragments: z.array(z.object({
    type: z.string().max(100), text: z.string().max(10000),
    emote: z.object({ id: z.string().max(200) }).optional(),
  })).max(500).optional() }),
  badges: z.array(z.object({ set_id: z.string().max(100) })).max(100),
});

async function boundedBody(request: Request) {
  const reader = request.body?.getReader();
  if (!reader) return "";
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.length;
      if (size > 262144) { await reader.cancel(); throw new Error("too_large"); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  return new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks));
}

export async function POST(request: NextRequest) {
  let rawBody: string;
  try { rawBody = await boundedBody(request); }
  catch { return new NextResponse("invalid body", { status: 413 }); }
  const messageId = request.headers.get("Twitch-Eventsub-Message-Id") ?? "";
  const timestamp = request.headers.get("Twitch-Eventsub-Message-Timestamp") ?? "";
  if (!messageId || messageId.length > 200 || !verifyEventSubSignature({
    messageId, timestamp, signature: request.headers.get("Twitch-Eventsub-Message-Signature"),
  }, rawBody, env.twitchEventSubSecret) || !isFreshMessage(timestamp)) {
    return new NextResponse("invalid signature or timestamp", { status: 403 });
  }
  let body;
  try { body = JSON.parse(rawBody); } catch { return new NextResponse("invalid JSON", { status: 400 }); }
  const type = request.headers.get("Twitch-Eventsub-Message-Type");
  if (type === "webhook_callback_verification") {
    return typeof body.challenge === "string" && body.challenge.length <= 2048
      ? new NextResponse(body.challenge, { headers: { "Content-Type": "text/plain" } })
      : new NextResponse("invalid challenge", { status: 400 });
  }
  if (type === "revocation") {
    console.warn("EventSub subscription revoked");
    return new NextResponse(null, { status: 204 });
  }
  if (type !== "notification" || body.subscription?.type !== "channel.chat.message") {
    return new NextResponse(null, { status: 204 });
  }
  const parsed = chat.safeParse(body.event);
  if (!parsed.success) return new NextResponse("invalid event", { status: 400 });
  if (durableEventsEnabled()) {
    try { await enqueueEvent(messageId, parsed.data); }
    catch { return new NextResponse("queue unavailable", { status: 503 }); }
  } else if (!isDuplicateMessage(messageId)) {
    const streamer = await getStreamerByTwitchId(parsed.data.broadcaster_user_id);
    if (streamer) await processChatEvent(messageId, parsed.data, {
      streamer, settings: await getEntrySettings(streamer.id),
    }, false);
  }
  return new NextResponse(null, { status: 204 });
}
