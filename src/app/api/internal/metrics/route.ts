import { requireMonitoringAccess } from "@/lib/monitoringAuth";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export function GET(request: Request) {
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
  const body = metrics.map(([name, help, type, value]) =>
    `# HELP ${name} ${help}\n# TYPE ${name} ${type}\n${name} ${value}\n`,
  ).join("");

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; version=0.0.4; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}
