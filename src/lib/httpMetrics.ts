import "server-only";

type HttpSample = {
  method: string;
  route: string;
  status: string;
  count: number;
  sum: number;
  buckets: number[];
};
type HttpStore = { version: number; generation: string; boundaries: number[]; requests: Map<string, HttpSample> };

export function httpMetricsText(): string {
  const store = (globalThis as unknown as Record<symbol, HttpStore | undefined>)[Symbol.for("koipond.http.metrics")];
  const lines = [
    "# HELP koipond_http_instrumentation_enabled Whether HTTP process instrumentation is loaded.",
    "# TYPE koipond_http_instrumentation_enabled gauge",
    `koipond_http_instrumentation_enabled ${store?.version === 1 ? 1 : 0}`,
  ];
  if (!store || store.version !== 1) return lines.join("\n") + "\n";
  lines.push(
    "# HELP koipond_http_requests_total Completed application requests, excluding operational probes and activity heartbeats.",
    "# TYPE koipond_http_requests_total counter",
    "# HELP koipond_http_request_duration_seconds Completed application request duration in seconds.",
    "# TYPE koipond_http_request_duration_seconds histogram",
  );
  for (const sample of store.requests.values()) {
    const labels = `method=${JSON.stringify(sample.method)},route=${JSON.stringify(sample.route)},status=${JSON.stringify(sample.status)},process_generation=${JSON.stringify(store.generation)}`;
    lines.push(`koipond_http_requests_total{${labels}} ${sample.count}`);
    store.boundaries.forEach((boundary, index) => {
      lines.push(`koipond_http_request_duration_seconds_bucket{${labels},le="${boundary}"} ${sample.buckets[index]}`);
    });
    lines.push(
      `koipond_http_request_duration_seconds_bucket{${labels},le="+Inf"} ${sample.count}`,
      `koipond_http_request_duration_seconds_sum{${labels}} ${sample.sum}`,
      `koipond_http_request_duration_seconds_count{${labels}} ${sample.count}`,
    );
  }
  return lines.join("\n") + "\n";
}
