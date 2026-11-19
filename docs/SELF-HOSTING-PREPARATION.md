# Koipond: isolated migration preparation

Status 2026-11-19: local draft only. No push, cloud deployment, infrastructure
change, production SQL, DNS change or EventSub subscription mutation.

## Verified baseline

- GitHub main: d723d8d74ad6e3c51eed1797153f872581e2b34b; Leon confirms production.
- GitHub CLI is authenticated and can access the repository; no additional token required.
- Vercel: https://vercel.com/t1ggys-projects/koiproject (credentials still required).
- Supabase: xlatvbaiwfftkxgtzrly (scoped read-only management access still required).
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
- Compose draft syntax validated with --no-env-resolution and the example file;
  a real staging environment file has deliberately not been created.
- Compose staging configuration binds only to loopback, has no scheduler, and
  expects an independent staging database and test-only OAuth/webhook credentials.
- Production secrets must never be build arguments. NEXT_PUBLIC_* values are
  deliberately public build inputs; changing Supabase endpoints requires rebuilding.
- Base image digest and production Node version must be confirmed before deployment.

## Multi-instance and monitoring work still pending

No live metrics, worker, queue or migration has been implemented yet.

1. Add bounded private readiness and aggregate metrics; no sensitive responses.
2. Add durable atomic EventSub ingress deduplication and leased worker processing.
3. Validate per-channel ordering and transactional giveaway mutations under concurrency.
4. Coordinate SESSION_SECRET, Server Action encryption keys, cache and release draining.
5. Make stats scheduling singleton with lease/overlap protection.
6. Track active users with a documented time window and shared expiring state.
   Keep user/channel identifiers out of Prometheus labels.
7. Keep metrics collection on existing monitoring infrastructure over private access.
8. Build isolated acceptance tests before pushing or deploying anything.

## Cloud inventory needed

Vercel: project settings, deployed commit, Node/build settings, region, domains,
cron configuration and environment variable NAMES/scopes; no initial secret export.

Supabase: region/version, health, database size and schema, migration history,
RLS/grants/publications, storage usage/configuration and available backups/logs.
Use a token scoped to this project with read-only permissions. Production service
role keys and database passwords are not needed for this initial inventory.

## Explicit production gate

Neither main nor a Vercel-connected branch is pushed in this phase. Even a preview
deployment can accidentally point at production Supabase or steal a test channel's
EventSub subscription if it reuses production configuration. All staging resources
must be independent. Production cutover requires passing tests AND Leon's approval.
