# Koipond monitoring draft

Historical instrumentation design. The private integration is now deployed on
the existing monitoring VM: job koipond_stage, two fixed per-instance HTTPS
scrapes, dashboard koipond-stage and six Grafana alerts using the existing
Telegram contact point. Readiness and database-backup health use the existing
node exporter. The deployed dashboard and rules are mirrored in deploy/monitoring.
See MONITORING-PROMOTION.md for the inactive production-promotion procedure.

Nothing in this document changes the existing Prometheus/Grafana installation.

## Implemented locally

- GET /health/live: process liveness, no external dependency.
- GET /health/ready: authorized, bounded HEAD query through the configured
  Supabase REST gateway; 503 on missing config, dependency failure or timeout.
- GET /api/internal/metrics: authorized Prometheus process memory/CPU/uptime,
  completed HTTP requests and request duration histograms.
- Monitoring routes require KOIPOND_MONITORING_TOKEN with at least 32 characters.
  Missing token fails closed with 503; incorrect authorization returns 401.
- Readiness does not follow upstream redirects or return upstream error details.
- SUPABASE_INTERNAL_URL is an optional runtime server-side override used by
  application Supabase calls and readiness. Browser Realtime still uses the
  public NEXT_PUBLIC_SUPABASE_URL compiled into the client bundle.

The runtime smoke test uses only a loopback mock and tests healthy, failed,
redirecting and slow dependencies as well as authorization failures. It is not
a real Supabase/Reatime acceptance test.

## HTTP instrumentation

Load scripts/http-metrics.cjs before the Node HTTP server. The Docker runner
copies this to observability.cjs and starts node --require ./observability.cjs
server.js. Without the preload, koipond_http_instrumentation_enabled is zero;
the endpoint does not silently claim to have observed requests.

The preload records completed responses, including failures and redirects.
It groups routes into /, /admin, /api/admin, /api/auth, /api/eventsub, /overlay,
/demo, /static and other. It retains neither raw paths nor query strings or
headers. /health/*, /api/internal/* and /api/activity are excluded from
application request totals. Durations cover server dispatch to response finish.

Counters: koipond_http_requests_total{method,route,status}.
Histogram: koipond_http_request_duration_seconds with finite buckets at
0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5 and 10 seconds, plus +Inf.

Run node scripts/check-monitoring.mjs to verify request/status accounting,
duration measurement, operational-request exclusion and identifier redaction
against a real loopback Node HTTP server. No cloud credentials are used.

For full service integration, scripts/check-multi-instance.mjs targets two
already-running isolated web instances plus a real isolated PostgREST gateway.
It seeds only fixed synthetic fixture identities and verifies a shared signed
session, duplicate EventSub ingestion, a single worker claim/entry, persisted
deduplication after processing, activity Origin/auth checks, shared distinct-user
metrics and invalid/stale/malformed webhook rejection.
It also exercises !pickwinner through the durable worker and replays the command
on both instances without a second draw, then submits competing correct number
guesses and checks one committed draw plus one round/session transition.

Required environment: KOIPOND_TEST_WEB_URLS=http://web1:3000,http://web2:3000,
KOIPOND_TEST_API_URL=http://gateway:8000, KOIPOND_TEST_SERVICE_KEY (synthetic
service-role JWT), SESSION_SECRET, TWITCH_EVENTSUB_SECRET,
NEXT_PUBLIC_APP_URL=http://koipond.test, KOIPOND_TEST_WORKER_TOKEN and
KOIPOND_TEST_MONITORING_TOKEN. The last two may instead use KOIPOND_WORKER_TOKEN
and KOIPOND_MONITORING_TOKEN. Both web processes must share the session/event
secrets and KOIPOND_METRICS_PSEUDONYM_SECRET, enable durable mode, and load the
HTTP preload. Apply migration 0017 before invoking the test. Its gateway must
mock Realtime broadcast delivery while forwarding REST to the real PostgREST.
Set KOIPOND_ISOLATED_TEST=1 and load the parent-provided test-external-mock.cjs
into both web processes. That test-only preload must answer Twitch token/chat
requests synthetically and deny every other external outbound destination. This
preload is mounted only for tests and is not part of the runtime image.

The test accepts only fixed Docker hostnames or .test hosts, refuses URL
credentials/redirects and rejects the known production project's JWT reference.
It must run in a fresh isolated queue environment without background polling
workers because it asserts the exact claim count. Synthetic fixture rows remain
until the parent removes the isolated database. Command processing is allowed
only with the explicit isolated-test flag and the confirmed external-request mock.

## Shared operational metrics and user counting

When KOIPOND_DURABLE_EVENTS=true, metrics additionally reads the privileged
koi_operational_metrics RPC. Missing or invalid database measurements return
503 instead of publishing invented zeros.

- koipond_queue_pending, koipond_queue_processing, koipond_queue_dead
- koipond_queue_oldest_age_seconds
- koipond_events_received_total, koipond_events_completed_total
- koipond_events_retried_total, koipond_events_duplicate_total
- koipond_active_users, koipond_active_streamers

These are database-wide values repeated by each web instance: use max across
instances, never sum. Request counters/histograms and process metrics are per
instance and can be summed where appropriate. For global event counter rates,
deduplicate per-instance rates rather than adding them.

POST /api/activity requires the exact configured application Origin, a valid
signed session and a dedicated KOIPOND_METRICS_PSEUDONYM_SECRET of at least
32 characters. It HMAC-hashes the Twitch identity before calling koi_presence_touch;
neither identity nor hash is returned as a metric label. All instances must share
this secret to deduplicate users correctly. Anonymous activity returns 401.
Disabled durable mode returns 204 without any database call.

The browser sends at most one heartbeat per minute while visible and while an
input/visibility activation occurred within the previous five minutes. It stops
after an anonymous 401. Closing a tab does not send a fragile decrement: database
activity expires after five minutes. Multiple tabs and instances count one user.
This measures signed-in recently active users, not anonymous visitors, Twitch
audience size or number of Realtime sockets. Telemetry failures do not block UI.

## Deployment constraints

- Keep metrics and readiness private at the reverse proxy as well as token-protected.
- Existing monitoring host 10.1.0.84 collects metrics; no new monitoring stack.
- Only liveness is suitable as a container restart health check. A database outage
  should withdraw readiness and alert, not continuously restart healthy processes.
- Add an instance label per target. CPU counters and process memory are per instance.
- Coordinate public Supabase build inputs and server runtime configuration per environment.

## Still required

1. Deploy the prepared HTTP/process/operational metrics only to isolated staging.
2. Validate active users across two instances and signed-session Origin checks.
3. Add provider-level Realtime connection counts separately from active users.
4. Add singleton scheduler last-success time and refresh backlog.
5. Readiness/Realtime functional probes and backup/restore monitoring.
6. One Koipond Grafana dashboard on the existing platform; no production changes yet.

Do not use user IDs, channel names, secrets, arbitrary URLs or query strings as
Prometheus labels. Do not log OAuth codes, authorization headers or chat payloads.
