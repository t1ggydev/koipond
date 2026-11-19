# Koipond self-hosting preparation

Approved scope: finish the branch, Jenkins/Ansible, private application and
Supabase deployment, data-copy migration, monitoring, backups and acceptance
tests. Public traffic and live writes stay on Vercel/Supabase Cloud until Leon
explicitly approves the final production cutover. No merge into main yet.

## Tasks

- [x] Reproducible non-root container packaging with pinned Node base image.
- [x] Read-only cloud inventory and schema replay comparison.
- [x] Protected readiness, HTTP/process/queue metrics and deduplicated active-user counts.
- [x] Durable EventSub ingestion, per-channel leased workers and atomic draw operations.
- [x] Shared signed sessions, stable Server Action build keys and immutable two-instance images.
- [x] Singleton statistics scheduling with overlap protection.
- [x] Self-hosted Supabase, seventeen migrations and verified eight-table data restore.
- [x] Encrypted Cloud application snapshot and isolated restore test.
- [x] Daily local platform/database/storage backups and isolated complete recovery test.
- [x] Private TLS edge and direct DNS01 certificate renewal on edge01.
- [x] Dedicated Jenkins preparation pipeline and Ansible deployment/rollback tasks.
- [x] SQL/RLS/concurrency checks and two-instance integration acceptance suite.
- [x] Complete a successful end-to-end Jenkins staging deployment (build 3).
- [x] Verify real deployed REST and Realtime broadcasts/database changes (16 checks, repeated after reboot).
- [x] Integrate fixed instance scrape targets and backup health into existing Grafana.
- [x] Exercise all three private VM restarts and take fresh encrypted PBS snapshots.
- [x] Build inactive canonical production image and record its immutable identity.
- [x] Review gated production activation; rehearse reversible write fencing and final-sync rollback in isolation.
- [x] Replace main's Vercel requirement with native Jenkins PR-head checks using a restricted folder credential.
- [x] Move builds to a separate agent, upgrade Jenkins/agent Java to 25 and enforce UI CSP.
- [x] Verify all nine VMs and resource reserves; persist private Jenkins temp paths and reserve-checked swap.
- [x] Group Grafana by site with VM details,35 Docker components and native application/database monitoring.
- [x] Validate correlated alarms with synthetic failure/negative fixtures and the existing receiver configuration.
- [ ] Obtain Leon's explicit Go-live approval before production DNS/live-write switch.

## References

- Production baseline: d723d8d74ad6e3c51eed1797153f872581e2b34b.
- Vercel project: https://vercel.com/t1ggys-projects/koiproject
- Supabase project: https://supabase.com/dashboard/project/xlatvbaiwfftkxgtzrly
- Preparation branch: prep/koipond-self-hosting, pushed; automatic Vercel deployment disabled.
- Private application: https://koipond-stage.kluge-it.tech
- Private API: https://koipond-api.kluge-it.tech
- Deployment: DEPLOYMENT.md; Supabase recovery: SUPABASE-DEPLOYMENT.md.

The Cloud copy remains authoritative. The private database is a dated snapshot,
not continuous replication. Final cutover requires a fresh consistent snapshot
while old writers are quiesced; never permit independent writes on both databases.
A DNS-only rollback after new writes would lose data and is forbidden.
