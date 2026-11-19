# Koipond: isolated migration preparation

Historical initial inventory. Its "local draft" and pending-work statements
describe the first audit, not current deployment status. See PROJECT.md,
DEPLOYMENT.md and GO-LIVE.md for the subsequently implemented and tested state.

Status 2026-11-19: local draft only. No push, cloud deployment, production
service configuration, production SQL writes, DNS change or EventSub mutation.

## Verified baseline

- GitHub main: d723d8d74ad6e3c51eed1797153f872581e2b34b; Leon confirms production.
- GitHub CLI is authenticated and can access the repository; no additional token required.
- Vercel: https://vercel.com/t1ggys-projects/koiproject; authenticated reads confirmed.
- Supabase: xlatvbaiwfftkxgtzrly; management token and read-only SQL confirmed.
- Vercel production commit matches main, Node 24.x, function region fra1.
- Canonical domain www.koipond.live; bare domain redirects with HTTP 308.
- Existing stage.koipond.live custom environment was discovered and left unchanged.
- Preview and production share several credential entries, including the service
  role key. Do not assume Vercel preview is an isolated database test environment.
- Hosted Supabase is healthy in eu-central-1 on PostgreSQL 17.6.1.155.
- Production schema has eight RLS-enabled app tables, 85 columns, 23 indexes,
  22 constraints, and the draws-only supabase_realtime publication.
- Empty Storage and Supabase Auth user counts confirmed by aggregate read-only SQL.
- Backup API listed zero backups; PITR is false. This does not rule out backups
  outside the platform. A verified data backup is required before data migration.
- pve01: approximately 64 GB RAM, 39 GB available at inventory time; ZFS healthy.
- edge01: 2 GB RAM, approximately 1.5 GB available; 20 GB free root filesystem.
- apps01: 8 GB RAM, approximately 7.3 GB available; 73 GB free root filesystem.
- supabase01: 12 GB RAM, approximately 10.7 GB available; 168 GB free root filesystem.
- No running Docker containers on those three VMs at inventory time.
- PBS snapshots for VM 120 and 130 include 2026-11-19 02:15 UTC.
  Snapshot listing was checked; no new restore test was performed in this phase.

## Local preparation and validation

- Branch prep/koipond-self-hosting, not pushed.
- Original lockfile fails npm 11.19.1 clean install with missing emnapi entries.
- Repaired lockfile installs cleanly; no previously locked package version changed.
- Node 24.22.0 production build passes with standalone output.
- Lint: zero errors, two pre-existing unused-symbol warnings in admin/page.tsx.
- Build had no cloud credentials. Homepage catches missing Supabase configuration
  and omits the participant count. This is not a database or application acceptance test.
- Isolated standalone runtime on loopback passed: liveness 200, unauthenticated
  admin entries 403, unconfigured cron 503, disabled demo API 404.
- Runtime process was stopped after checks.
- Docker Desktop Linux engine is not running locally. Ansible built the draft on
  the existing apps01 Docker engine instead, with no production secrets.
- Linux image koipond-prep:d81ab2eeb34d (amd64) built successfully:
  sha256:9a42b815b784d093a30f392b01002d0ef0470c8769503c781c8a6bca4f90361e.
- Disposable Linux smoke container passed the same four endpoint checks with
  network=none, no published ports, a memory/CPU limit and non-root user node.
- Test container and owned temporary source directory were removed. Only the
  preparation image/build cache remain on apps01. No persistent app service runs.
- A second image, koipond-prep:fe974bc44407, passed twelve runtime integration
  checks on Linux, including protected metrics/readiness, failure/timeout/redirect
  handling and fail-closed behavior. Its dependency was a local mock, not Supabase.
- All 16 repository SQL migrations replayed in disposable PostgreSQL 17 with
  no network or host ports. All 85 columns, 23 indexes and 22 constraints match
  the hosted metadata; RLS visibility/write denial and publication checks passed.
- Cloud has 17 migration records: the extra enable_rls_settings migration repeats
  the settings RLS activation already present in repository migration 0001.
- Replay used synthetic data only; the temporary database and source were removed.
- Compose draft syntax validated with --no-env-resolution and the example file;
  a real staging environment file has deliberately not been created.
- Compose staging configuration binds only to loopback, has no scheduler, and
  expects an independent staging database and test-only OAuth/webhook credentials.
- Production secrets must never be build arguments. NEXT_PUBLIC_* values are
  deliberately public build inputs; changing Supabase endpoints requires rebuilding.
- Production Node major version is confirmed as 24. Base image digest must still
  be pinned before a deployment is approved.

## Multi-instance and monitoring work still pending

Protected process metrics and database readiness are implemented and tested only
in the preparation branch. They are not deployed to production or Grafana.
HTTP/business/user metrics, durable worker/queue and scheduling are still pending.

1. Integrate the prepared private readiness and process metrics into staging.
2. Add durable atomic EventSub ingress deduplication and leased worker processing.
3. Validate per-channel ordering and transactional giveaway mutations under concurrency.
4. Coordinate SESSION_SECRET, Server Action encryption keys, cache and release draining.
5. Make stats scheduling singleton with lease/overlap protection.
6. Track active users with a documented time window and shared expiring state.
   Keep user/channel identifiers out of Prometheus labels.
7. Keep metrics collection on existing monitoring infrastructure over private access.
8. Build isolated acceptance tests before pushing or deploying anything.

## Cloud inventory result

Vercel: read project settings, deployed commit, Node/build settings, region,
domains, cron configuration and environment variable NAMES/scopes. No secret export.

Supabase: read region/version, four services' health, database size/schema,
migration history, RLS/grants/publications, indexes/constraints, aggregate row and
storage/auth counts, and backup status. No user rows or chat bodies were fetched.
Production service role keys and database passwords were not used for this audit.

## Explicit production gate

Neither main nor a Vercel-connected branch is pushed in this phase. Even a preview
deployment can accidentally point at production Supabase or steal a test channel's
EventSub subscription if it reuses production configuration. All staging resources
must be independent. Production cutover requires passing tests AND Leon's approval.
