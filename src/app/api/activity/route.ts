import { createHmac } from "node:crypto";
import { env } from "@/lib/env";
import { getSession } from "@/lib/session";
import { getStreamerByTwitchId } from "@/lib/streamers";
import { supabaseAdmin } from "@/lib/supabaseServer";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const headers = { "Cache-Control": "no-store" };
export async function POST(request: Request) {
  if (process.env.KOIPOND_DURABLE_EVENTS !== "true") return new Response(null, { status: 204, headers });
  try {
    if (request.headers.get("origin") !== new URL(env.appUrl).origin) {
      return Response.json({ status: "forbidden" }, { status: 403, headers });
    }
    const session = await getSession();
    if (!session) return Response.json({ status: "unauthorized" }, { status: 401, headers });
    const secret = process.env.KOIPOND_METRICS_PSEUDONYM_SECRET;
    if (!secret || secret.length < 32) return Response.json({ status: "unavailable" }, { status: 503, headers });
    const subject = createHmac("sha256", secret).update(session.twitchId).digest("hex");
    const streamer = await getStreamerByTwitchId(session.twitchId);
    const { error } = await supabaseAdmin().rpc("koi_presence_touch", {
      p_subject_hash: subject,
      p_streamer: streamer !== null,
    }).abortSignal(AbortSignal.timeout(2000));
    if (error) throw new Error("Presence update unavailable");
    return new Response(null, { status: 204, headers });
  } catch {
    return Response.json({ status: "unavailable" }, { status: 503, headers });
  }
}
