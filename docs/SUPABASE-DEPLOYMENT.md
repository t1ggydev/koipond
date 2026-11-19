# Private Supabase deployment for Koipond

Prepared 2026-11-19 on `supabase01` (`10.10.10.30`, Proxmox VM130). Vercel and the Supabase Cloud project remain the production systems. This stack is a private staging/recovery destination; DNS, OAuth callbacks and Twitch subscriptions have not been switched.

## Reproducible release

The vendored official Supabase Docker configuration is pinned to upstream commit `f9d99e6bf073742af380899c30e77c3f883f69bb`. See the [official self-hosting documentation](https://supabase.com/docs/guides/self-hosting/docker) and [pinned upstream Docker files](https://github.com/supabase/supabase/tree/f9d99e6bf073742af380899c30e77c3f883f69bb/docker). The PostgreSQL17/Kong variants are combined by `deploy/supabase/render-compose.py`. `images.lock.yml` pins every runtime image to its resolved registry digest. PostgreSQL is Supabase17.6.1.136 (server reports17.6), compatible with the Cloud17.6 source.

Eleven services are deployed: PostgreSQL, Auth, PostgREST, Realtime, Storage, imgproxy, postgres-meta, Edge Runtime, Supavisor, Kong and Studio. Logflare, Analytics and Vector are excluded; the existing home monitoring remains central. Resource limits fit the12GB VM. Docker logs rotate locally.

The runtime directory is `/srv/koipond-supabase`, owned by root. `koipond-supabase.service` is enabled for boot recovery. This deployment does not modify the production Cloud database.

## Access and credentials

* API gateway: `10.10.10.30:8000`, bound exclusively to that private address. Guest firewall restricts the sources to app/edge hosts. Planned external URL: `https://koipond-api.kluge-it.tech`; TLS/DNS are a separate final preparation step.
* Studio: `127.0.0.1:3001` on supabase01, reachable through an authorized SSH forward. Kong exposes no Studio/dashboard, metadata or MCP route.
* PostgreSQL container has no published port. Supavisor ports5432/6543 bind to localhost only.
* Controller Vault: `/home/leon/homelab-ansible/root-infra/recovery/koipond/supabase-self-hosted-secrets.vault`. Its JSON `environment` holds the generated `ANON_KEY`, `SERVICE_ROLE_KEY`, `JWT_SECRET`, `POSTGRES_PASSWORD`, Studio credentials and encryption keys.
* Server environment: `/srv/koipond-supabase/.env`, root0600. Never print it, commit it, send it through chat or place service-role keys in browser runtime configuration.

Secrets are independent from Supabase Cloud. Legacy HS256 API JWTs expire two years after creation and must be renewed before expiry; opaque/asymmetric API keys are currently disabled. App-owned authentication remains unchanged; Supabase public signup/email/phone sign-in are disabled. No production SMTP or Twitch credentials were installed in this stack.

## Deployment and database preparation

The controller is `/home/leon/homelab-ansible/root-infra`; existing inventory, pinned SSH host keys, `infra` user and sudo are reused. Stage this repository's files under `work/supabase-source` and supply `work/supabase-source-vars.json` with `supabase_source`, the relative source-file/directory lists and the migration source/list. `prepare-secrets.py` runs on the controller, reads the existing protected Vault helper and writes a temporary0600 runtime environment; delete the temporary environment after the playbook succeeds.

Run `deploy/ansible/supabase-prepare.yml` with those source variables. The playbook copies protected configuration, starts pinned images, applies0001 through0017 using a private checksum registry and verifies the API. Re-running refuses to silently alter an applied migration. Do not run an upstream reset script against this persistent stack.

`restore-snapshot.py` runs on the controller. It decrypts the existing application snapshot only in memory and streams it over pinned SSH. It restores only the eight public application tables using85 explicit baseline columns;0017's new columns, platform schemas and private queue remain intact. It permits only an empty target or the exact initial seed row, and otherwise accepts only an already identical restore. An unrelated populated target is rejected. The restored snapshot is from2026-11-19 18:50:42UTC; it is a test copy, not an ongoing replication feed. A fresh final source snapshot and write-freeze/reconciliation are required at the cutover gate.

Restored counts: participants168, streamers2, osu_stats168, settings2, presets0, entries5150, draws446, winner_messages3019. Every table's canonical JSON hash matches the encrypted source snapshot. OAuth/EventSub identifiers in restored rows are inert data; do not start subscription management against them before the explicit production gate.

## Backups and recovery

`deploy/ansible/supabase-backup.yml` installs `backup.py`, `koipond-supabase-backup.service` and the daily01:00 timer (server timezone, up to120seconds random delay). Each run creates `pg_dump -Fc` archives for both `postgres` and `_supabase`, a globals dump containing role definitions/password hashes, and an archive of `.env`, pinned configuration, upstream scripts, application migrations, Storage files and snippets. Files are root0600 in `/var/backups/koipond-supabase`, directory0700. Publication uses an atomic rename; the newest seven archives are retained and `latest.tar.gz` is an atomic symlink. No backup secret is passed in a command argument. Backups are sensitive recovery material.

Existing PBS job `pbs-daily-pve01` is enabled daily03:15, includes VM130 explicitly and targets `pbs-pve01`. VM130's180GB scsi0 disk has no `backup=0` exclusion. Therefore these local archives, configuration and live storage are included in the VM backup. A newer PBS snapshot must be triggered/confirmed by infrastructure automation before the final go-live gate; this task did not change the existing job or retention policy.

`test-backup-restore.py` restores globals and both database dumps into a disposable PostgreSQL17 container using the exact pinned Supabase database image. It has `--network none`, no host ports, disabled container logs and tmpfs database storage. It verifies all eight application row counts,17 migration records, auth/storage/realtime schema presence and denied anonymous operational RPC execution; it validates archived-file hashes and removes the test container afterward. The initial complete local archive passed this probe. Storage was empty at this test; files are included in the archive, but no nonempty object payload was available for a round-trip download test.

For real recovery, stop all application writers, restore the globals and both databases on the same pinned PostgreSQL image, restore the protected configuration and Storage files, then run `verify.py` and private app tests before starting workers. Never overwrite the Cloud source. Keep the existing stack/configuration and PBS restore point until validation completes; a rollback before cutover leaves Vercel/Cloud serving users. After cutover, reconcile any new writes before reverting to an older database.

## Recorded checks and remaining gate

`verification.json`: eleven healthy services; Auth/REST/Storage200; dashboard/meta/MCP404; operational metrics200 for service role and401 for anon; PostgreSQL17.6, eight app tables,17 migrations.

`restore-verification.json`: encrypted-source application snapshot hashes and counts matched,85 explicit columns, platform/private queue preserved.

`full-restore-verification.json` on supabase01: both local database dumps and globals restored successfully in isolation, configuration hashes matched, application counts and permissions passed.

Still required before production approval: staged app/worker integration with these own keys; TLS/private edge wiring; fresh migration snapshot/reconciliation; prepared external URL and OAuth callback validation without changing active production callbacks; verified PBS snapshot; end-to-end staged application checks; then the user's explicit go-live approval. Existing Vercel/Cloud and productive Twitch subscriptions remain active throughout preparation.

### Backup monitoring

The root backup runner atomically publishes `/var/lib/prometheus/node-exporter/koipond-supabase-backup.prom` with mode0644. It contains no credentials or database contents. Gauges are `koipond_supabase_backup_last_success_timestamp_seconds`, `koipond_supabase_backup_size_bytes` and `koipond_supabase_backup_last_run_success`. A run marks success0 before starting its backup work; only completed archive publication changes it to1 and advances success time/size. Failure or interruption preserves the preceding successful timestamp and size. These metrics describe this self-hosted **staging** stack, not the still-active Cloud production database. Existing centralized Prometheus/Grafana collect them through node_exporter; the monitoring agent maintains the associated age/failure panels and alerts.

The monitored backup deployment was checked through Ansible: initial run succeeded, all three gauges were present, last_run_success=1 and the textfile mode was0644. The backup archive and protected runtime environment retain their restrictive root permissions.

### Realtime end-to-end validation

The deployed application smoke test passed16 checks on2026-11-19 after waiting for Realtime's `system` event with `extension=postgres_changes` and `status=ok`. A channel reporting `SUBSCRIBED` alone did not establish that the database change subscription was ready; inserting immediately caused an initial delivery timeout. After the readiness event, both a real WebSocket broadcast and an anonymous `postgres_changes` INSERT notification arrived. The synthetic unscoped draw was removed in cleanup.

Server-side inspection confirmed `wal_level=logical`, publication `supabase_realtime` containing `public.draws`, active wal2json and Broadcast pgoutput slots, anonymous SELECT privileges and the expected draws read policy. No replication error was observed. No server configuration or permissions were changed for this investigation; the test now waits for actual replication readiness.
