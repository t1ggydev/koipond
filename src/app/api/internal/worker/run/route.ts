import { timingSafeEqual } from "node:crypto";
import { processChatEvent } from "@/lib/eventsubProcessor";
import { toEntrySettings } from "@/lib/entries";
import { withExecutionSignal } from "@/lib/boundedFetch";
import {
  claimEvent, durableEventsEnabled, eventHeartbeat, finishEvent,
} from "@/lib/operationalQueue";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

export async function POST(request: Request) {
  const token = process.env.KOIPOND_WORKER_TOKEN;
  const headers = { "Cache-Control": "no-store" };
  if (!durableEventsEnabled() || !token || token.length < 32) {
    return Response.json({ status: "unavailable" }, { status: 503, headers });
  }
  const supplied = Buffer.from(request.headers.get("authorization") ?? "");
  const expected = Buffer.from(`Bearer ${token}`);
  if (supplied.length !== expected.length || !timingSafeEqual(supplied, expected)) {
    return Response.json({ status: "unauthorized" }, { status: 401, headers });
  }
  let claim;
  try { claim = await claimEvent(); }
  catch { return Response.json({ status: "unavailable" }, { status: 503, headers }); }
  if (!claim) return Response.json({ processed: false }, { headers });
  const { job, owner } = claim;
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 45_000);
  let extending = false;
  const heartbeat = setInterval(() => {
    if (extending) return;
    extending = true;
    void eventHeartbeat(job.id, owner)
      .then((owned) => { if (!owned) abort.abort(); })
      .catch(() => abort.abort())
      .finally(() => { extending = false; });
  }, 15_000);
  try {
    await withExecutionSignal(abort.signal, async () => {
      const ingress = job.context;
      if (!ingress || ingress.source !== "ingress") throw new Error("ingress_context_missing");
      if (ingress.ignored) return;
      if (!ingress.streamer || !ingress.settingsRow || ingress.streamer.twitchId !== job.channel_id) {
        throw new Error("ingress_context_invalid");
      }
      const context = { streamer: ingress.streamer, settings: toEntrySettings(ingress.settingsRow) };
      await processChatEvent(job.message_id, job.payload, context, true, job.received_at);
      abort.signal.throwIfAborted();
    });
    if (!await finishEvent(job.id, owner, true)) throw new Error("lease_lost");
    return Response.json({ processed: true, status: "complete" }, { headers });
  } catch {
    await finishEvent(job.id, owner, false).catch(() => false);
    console.error("Queued EventSub processing failed");
    return Response.json({ processed: true, status: "retry" }, { status: 503, headers });
  } finally {
    clearInterval(heartbeat);
    clearTimeout(timeout);
  }
}
