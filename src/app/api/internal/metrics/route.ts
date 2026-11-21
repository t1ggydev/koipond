import { requireMonitoringAccess } from "@/lib/monitoringAuth";
import { httpMetricsText } from "@/lib/httpMetrics";
import { supabaseAdmin } from "@/lib/supabaseServer";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(request: Request) {
  const denied = requireMonitoringAccess(request);
  if (denied) return denied;

  const memory = process.memoryUsage();
  const cpu = process.cpuUsage();
  const metrics: Array<[string, string, "gauge" | "counter", number]> = [
    ["koipond_process_uptime_seconds", "Process uptime in seconds.", "gauge", process.uptime()],
    ["koipond_process_resident_memory_bytes", "Resident memory in bytes.", "gauge", memory.rss],
    ["koipond_process_heap_used_bytes", "Used JavaScript heap in bytes.", "gauge", memory.heapUsed],
    ["koipond_process_heap_total_bytes", "Allocated JavaScript heap in bytes.", "gauge", memory.heapTotal],
    ["koipond_process_cpu_user_seconds_total", "User CPU time in seconds.", "counter", cpu.user / 1e6],
    ["koipond_process_cpu_system_seconds_total", "System CPU time in seconds.", "counter", cpu.system / 1e6],
  ];
  let body = metrics.map(([name, help, type, value]) =>
    `# HELP ${name} ${help}\n# TYPE ${name} ${type}\n${name} ${value}\n`,
  ).join("");
  body += httpMetricsText();

  if (process.env.KOIPOND_DURABLE_EVENTS === "true") {
    try {
      const { data, error } = await supabaseAdmin().rpc("koi_operational_metrics").abortSignal(AbortSignal.timeout(2000));
      if (error || !data || typeof data !== "object") throw new Error("Unavailable operational metrics");
      const globalMetrics: Array<[string, string, "counter" | "gauge", string]> = [
        ["koipond_queue_pending", "Globally pending events.", "gauge", "queue_pending"],
        ["koipond_queue_processing", "Globally processing events.", "gauge", "queue_processing"],
        ["koipond_queue_dead", "Globally exhausted events requiring review.", "gauge", "queue_dead"],
        ["koipond_queue_oldest_age_seconds", "Age of the oldest pending or processing event.", "gauge", "queue_oldest_age_seconds"],
        ["koipond_events_received_total", "Globally accepted events.", "counter", "events_received"],
        ["koipond_events_completed_total", "Globally completed events.", "counter", "events_completed"],
        ["koipond_events_retried_total", "Globally retried events.", "counter", "events_retried"],
        ["koipond_events_duplicate_total", "Globally rejected duplicate events.", "counter", "events_duplicate"],
        ["koipond_active_users", "Distinct signed-in active users within the last five minutes.", "gauge", "active_users"],
        ["koipond_active_streamers", "Distinct signed-in active streamers within the last five minutes.", "gauge", "active_streamers"],
        ["koipond_stats_last_success_timestamp_seconds", "Last successful scheduled statistics refresh.", "gauge", "stats_last_success_timestamp_seconds"],
        ["koipond_stats_failures_total", "Failed scheduled statistics refresh runs.", "counter", "stats_failures_total"],
      ];
      for (const [name, help, type, field] of globalMetrics) {
        const raw = data[field];
        if (typeof raw !== "number" && !(typeof raw === "string" && raw.trim() !== "")) {
          throw new Error("Missing operational metrics");
        }
        const value = Number(raw);
        if (!Number.isFinite(value) || value < 0) throw new Error("Invalid operational metrics");
        body += `# HELP ${name} ${help}\n# TYPE ${name} ${type}\n${name} ${value}\n`;
      }
    } catch {
      return Response.json({ status: "unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
    }
  }

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
