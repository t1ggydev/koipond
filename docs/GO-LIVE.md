# Koipond production gate and rollback

Status: private preparation is deployed. Vercel/Supabase Cloud remain authoritative.
No production merge, DNS switch, Cloud write fence or production-container start
is authorized until Leon explicitly approves Go-live.

## Prepared resources

| Component | Prepared location |
| --- | --- |
| Private application | https://koipond-stage.kluge-it.tech |
| Private API | https://koipond-api.kluge-it.tech |
| Edge | 10.10.10.10; public address 212.83.61.52 reserved, public ingress disabled |
| Staging app | apps01, 3100/3101, two identical web containers and two workers |
| Supabase | 10.10.10.30:8000; database/Studio local only; eleven healthy services |
| Production candidate | koipond-prod:cec716496146e2b1597bd1674c3a4020e1f66763-candidate |
| Candidate image ID | sha256:ec9b7352a0db0e90bfaf8187b051164ac64aba988d9f6953b00a7adac90de77a |
| Inactive production runtime | controller recovery/koipond/production-env-prepared.vault |
| Production ports after approval | 3200/3201, separate production compose project |
| Dashboard | http://10.1.0.84:3000/d/koipond-stage |

The production runtime preserves the existing signed-session secret and provider
credentials, uses the new self-hosted Supabase keys, independent operational
secrets, and a closed allowlist containing the two currently registered streamers.
Vercel does not export the sensitive original allowlist: any additional allowed
but not yet registered streamer must be explicitly added before its first login.
The active staging environment has invalid Twitch credentials and allowlist 0;
it cannot create real subscriptions or send Twitch chat messages.

## Remaining external prerequisites

1. DNS control of **koipond.live**. The existing Cloudflare credential is scoped
   to kluge-it.tech; querying it does not expose the koipond.live zone. A suitable
   scoped credential or authenticated DNS-console access is required for cutover.
   Never put tokens in chat or Git.
2. Cloud SQL write authority for the reversible writer fence. The current
   Supabase token executes as supabase_read_only_user and cannot manage table
   grants; a non-mutating capability preflight confirmed this. Provide a scoped
   database-write token or an authorized database-owner connection securely.
   Until then the fence scripts remain unexecuted and final sync must refuse.
3. A publicly trusted certificate for both koipond.live and www.koipond.live,
   including automatic renewal directly on edge01. The existing private/API
   certificate is valid for kluge-it.tech names only. The inactive edge config
   was syntax-tested with a clearly marked test certificate; this is not proof
   of production TLS readiness. Prefer DNS01 before moving traffic. HTTP01 would
   require an explicit challenge-routing phase and must not produce an HTTPS gap.
4. Confirm a quiet maintenance window without an active giveaway or winner-chat
   capture window, and explicit Go-live approval. The legacy application has no
   persisted EventSub deduplication history, so a cutover during active commands
   cannot promise cross-system exactly-once processing. Production
   OAuth callbacks retain their canonical URLs. Real interactive OAuth cannot be
   fully exercised on the private stage with its disabled Twitch credentials;
   it is a mandatory immediate production smoke test with rollback available.

## Exact order after approval

1. Export current DNS records, Vercel production alias/deployment identity and
   the Cloud schema/grant manifest. Verify source is still the original eight-table
   baseline. Confirm backup decryption/recovery keys remain available.
2. Obtain and verify the production TLS certificate before changing live DNS.
   Configure its unattended renewal and perform a renewal dry-run. Stage/API
   DNS01 renewal already runs directly on edge01.
3. Recheck Cloud migrations/schema, empty Auth/Storage assumptions and original
   streamer allowlist. New Auth users or Storage objects require an expanded
   migration; stop instead of silently omitting them.
4. Quiesce all staging web/worker containers and its statistics timer using
   apps-production-deploy.yml --tags quiesce. Keep production candidate inactive.
   Before this planned stop, disable the staging service expectations explicitly
   in apps01's root-only /var/lib/infrastructure-docker-metrics/expected.json or
   apply a bounded alert silence for only the affected staging components. The
   Docker collector retains missing durable services intentionally. Production
   service expectations are learned when the new long-running containers start;
   restore staging expectations if the cutover is rolled back. Do not suppress
   unrelated VM, database, mail or backup alarms.
5. Apply deploy/cutover/freeze-cloud.sql through the authorized Cloud SQL channel.
   This reversible, preflight-guarded transaction removes DML permissions from
   the three API roles while preserving reads and RLS. It stops old Vercel and
   cached clients writing during the copy. It has been tested only in isolated
   PostgreSQL; **it has not been applied to Cloud**. Drain in-flight requests,
   verify effective write denial and stable counts before taking the snapshot.
6. Export a fresh single-statement consistent application snapshot, encrypt it,
   verify its manifest, and restore all eight tables into the private database
   using explicit baseline columns. The current restore is dated 18:50:42 UTC;
   it is not a continuous mirror. Recheck every table's count/hash. The local
   outputs helpers Koipond-Finalsync-sichern.ps1 -RequireWriteFence and
   Koipond-Sicherung-auf-Controller-legen.ps1 use timestamped labels, DPAPI and
   Vault wrapping; the key travels only through encrypted SSH stdin. The exporter
   was verified to refuse the currently unfenced Cloud source. The controller
   restore helper accepts explicit --backup/--key-vault paths and requires both
   --final-sync and --go-live-approved; its transactional replacement, malformed
   manifest rollback, operational reset and platform preservation were tested in
   isolated PostgreSQL17. It has not been run against the active private stack.
7. With all consumers stopped, clear only the private staging operational queue,
   leases, presence and task-run/counter test state. Preserve platform schemas,
   restored application rows, subscriptions and production source identifiers.
   Record that no synthetic queued work remains before enabling production workers.
8. Take a final local database/platform backup and PBS snapshot. Start the pinned
   production candidate with apps-production-deploy.yml --tags deploy, supplying
   koipond_go_live_approved=true and koipond_final_sync_complete=true only after
   the evidence above exists. No Cloud service key belongs in the new runtime.
9. Validate readiness, real REST/Realtime, both instances, shared sessions and
   private metrics. Activate the production edge only after its real certificate
   chain/SAN/expiry/key gates pass. Its private metrics paths target 3200/3201.
10. Persist koipond_public_enabled=true in the infrastructure inventory and apply
    koipond-public-network.yml with explicit approval. The prepared nftables
    rules were syntax-checked in both modes; public DNAT is currently disabled.
    Public 80/443 go only to edge01. Keep SSH, database, Studio, worker and metrics private.
11. Switch the API A record to 212.83.61.52 and www/apex records to the new edge,
    preserving canonical www redirect. Check IPv6 records: do not leave an AAAA
    record pointing to the old provider. Keep mail and unrelated records unchanged.
12. Promote monitoring following the controller's
    files/koipond-monitoring/PRODUCTION-PROMOTION.md: use the production Bearer
    secret, production job/labels and fixed per-instance private scrape routes.
13. Test real Twitch/osu login, streamer authorization, EventSub signature and
    delivery, account linking, entry admission, draw/number modes, winner chat,
    OBS overlay, statistics schedule and external TLS. Leave the old Cloud writer
    fenced so cached old bundles cannot create a second authoritative database.
14. Observe errors/latency, queue age/retries/dead jobs, worker health and backup
    freshness. Retain Vercel, hosted Supabase and recovery material through the
    observation period. Do not delete or cancel them during initial cutover.

## Rollback boundaries

Before any new production writes, stop the new writers, restore prior edge/DNS
and Vercel aliases, verify the old deployment, then apply resume-cloud.sql to
restore the exact checked baseline DML grants. Confirm reads, writes and OAuth.

After new production writes, **do not perform a DNS-only rollback**. Quiesce new
writers, back up the new authoritative database, reconcile changed application
data back to Cloud, verify it, then resume Cloud writers and switch traffic. The
new operational columns stay out of the old baseline copy. Preserve source event
identifiers for reconciliation and deduplicate retried webhook delivery.

Prefer rolling back the application image while retaining the new authoritative
database when feasible. Never leave both databases accepting independent writes.
The prepared app/edge playbooks preserve their preceding configurations and
restore them if activation fails; they intentionally do not silently resume
staging consumers or override the database-authority decision.

## Credentials after the observation period

Rotate temporary GitHub/Vercel/Supabase access tokens, deployment SSH access and
Cloudflare tokens in a controlled order. Preserve backup decryption keys and
database/JWT secrets until dependent services and restore material have been
updated and verified. Session/JWT rotation can invalidate users and must be
planned; deleting recovery keys would make encrypted backups unrecoverable.
