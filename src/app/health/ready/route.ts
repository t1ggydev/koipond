import { requireMonitoringAccess } from "@/lib/monitoringAuth";
import { supabaseAdmin } from "@/lib/supabaseServer";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const denied = requireMonitoringAccess(request);
  if (denied) return denied;

  const headers = { "Cache-Control": "no-store" };
  const base = process.env.SUPABASE_INTERNAL_URL || process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  let database = false;
  if (base && key) {
    try {
      const url = new URL(`${base.replace(/\/+$/, "")}/rest/v1/participants`);
      url.search = "select=id&limit=1";
      const result = await fetch(url, {
        method: "HEAD",
        headers: { apikey: key, Authorization: `Bearer ${key}` },
        signal: AbortSignal.timeout(2000),
        redirect: "error",
        cache: "no-store",
      });
      database = result.ok;
    } catch {
      // Deliberately keep URLs, credentials and upstream errors out of responses.
    }
  }

  let queue = true;
  if (database && process.env.KOIPOND_DURABLE_EVENTS === "true") {
    try {
      const { error } = await supabaseAdmin().rpc("koi_operational_metrics")
        .abortSignal(AbortSignal.timeout(2000));
      queue = !error;
    } catch { queue = false; }
  }
  return Response.json(
    { status: database && queue ? "ready" : "unavailable", checks: { database, queue } },
    { status: database && queue ? 200 : 503, headers },
  );
}
