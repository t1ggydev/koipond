# Koipond monitoring draft

Nothing in this document changes the existing Prometheus/Grafana installation.

## Implemented locally

- GET /health/live: process liveness, no external dependency.
- GET /health/ready: authorized, bounded HEAD query through the configured
  Supabase REST gateway; 503 on missing config, dependency failure or timeout.
- GET /api/internal/metrics: authorized Prometheus process memory/CPU/uptime.
- Monitoring routes require KOIPOND_MONITORING_TOKEN with at least 32 characters.
  Missing token fails closed with 503; incorrect authorization returns 401.
- Readiness does not follow upstream redirects or return upstream error details.
- SUPABASE_INTERNAL_URL is an optional runtime server-side override used by
  application Supabase calls and readiness. Browser Realtime still uses the
  public NEXT_PUBLIC_SUPABASE_URL compiled into the client bundle.

The runtime smoke test uses only a loopback mock and tests healthy, failed,
redirecting and slow dependencies as well as authorization failures. It is not
a real Supabase/Reatime acceptance test.

## Deployment constraints

- Keep metrics and readiness private at the reverse proxy as well as token-protected.
- Existing monitoring host 10.1.0.84 collects metrics; no new monitoring stack.
- Only liveness is suitable as a container restart health check. A database outage
  should withdraw readiness and alert, not continuously restart healthy processes.
- Add an instance label per target. CPU counters and process memory are per instance.
- Coordinate public Supabase build inputs and server runtime configuration per environment.

## Still required

1. HTTP requests, status codes and latency from reverse proxy/application instrumentation.
2. Durable worker queue depth, oldest event age, retries and failure counters.
3. Active users with a documented five-minute window and shared expiring state.
4. Active streamers and Realtime connection counts, distinctly named.
5. Singleton scheduler last-success time and refresh backlog.
6. Readiness/Realtime functional probes and backup/restore monitoring.
7. One Koipond Grafana dashboard on the existing platform; no production changes yet.

Do not use user IDs, channel names, secrets, arbitrary URLs or query strings as
Prometheus labels. Do not log OAuth codes, authorization headers or chat payloads.
