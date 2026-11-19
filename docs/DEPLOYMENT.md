# Private self-hosting preparation

Production remains on Vercel and Supabase Cloud. This branch never merges main,
changes production DNS, or updates provider OAuth callbacks automatically.

## Topology

* edge01 10.10.10.10: private HTTPS for koipond-stage.kluge-it.tech and
  koipond-api.kluge-it.tech; their new DNS-only A records resolve to this private IP.
* apps01 10.10.10.20:3100/3101: two web instances using one immutable image ID.
  Separate worker processes use only internal Docker endpoints and a dedicated token.
* supabase01 10.10.10.30:8000: private self-hosted platform, own JWT/database keys.

Only 10.1.0.0/24 and 10.10.10.0/24 may use the edge. Public application ports and
Proxmox administration are not opened. `/api/internal/worker/` is blocked at edge;
metrics and readiness retain application Bearer authentication. Stable metrics
paths `/api/internal/metrics/instance1` and `/instance2` select one instance each.

## Secrets and build

Controller `recovery/koipond/staging-env.vault` holds the protected stage runtime.
The CI copy is `/home/jenkins-ci/koipond-secrets/staging.env` (0600), with Ansible
variables in `staging.vault.yml`; neither belongs in Git. The dedicated CI deploy
identity is restricted to connections from the controller and does not replace
existing management keys. Never print credentials or pass service-role keys as
Docker build arguments. Public URL/anon-key inputs are explicit. The stable
Server Actions key is provided through a temporary BuildKit secret outside the
build context, removed in an always block, and matches the runtime key.

Stage uses disabled Twitch credentials, streamer allowlist `0`, and its own
session/worker/monitoring/cron/pseudonym secrets. Thus restored application data
cannot accidentally create production Twitch subscriptions or send chat. Real
OAuth verification against the canonical hostname remains a final cutover check.

## Pipeline

Dedicated Jenkins job `Koipond-Preparation` reads `deploy/Jenkinsfile` only from
`prep/koipond-self-hosting`. Existing jobs and plugins are unchanged. The pipeline
checks out a clean exact commit on the controller, archives that commit, runs
isolated queue/RLS/two-instance acceptance tests on apps01, builds a unique image
tag with revision label, and optionally deploys private staging. Both web
instances and workers use the resolved immutable local image ID. There is no
production stage, DNS switch, merge, or webhook deployment trigger.

Initial bootstrap uses `apps-jenkins-bootstrap.yml` on the existing home Proxmox
host through its guest agent. It requires explicit branch readiness, independently
checks finished builds and no build processes, backs up Jenkins configuration and
job data, then performs a graceful restart. The one-use init script starts only
the dedicated job and deletes itself. Do not run bootstrap while builds are active.

## Operation and rollback

`apps-build-test.yml` accepts an explicit archive, revision and unique image tag.
`apps-deploy.yml` requires `koipond_deploy_staging=true`, preserves the preceding
compose/runtime files, validates configuration, starts with health checks and
restores the preceding private release on failure. Docker logs rotate at 10 MB,
three files per container. The application host refreshes osu statistics every
30 minutes through its authenticated local endpoint. Queue retention is bounded
inside the ingress RPC; no additional unbounded cleanup task is installed.

`edge-prepare.yml` creates only missing new private DNS records and refuses
conflicts. DNS01 issues a valid certificate without opening port 80. Its scoped
Cloudflare credential is 0600 on edge; renewal runs directly on edge daily with
jitter and reloads nginx only after validation. Images resolve to repository
digests. To remove this preparation, stop only compose projects
`koipond-staging` and `koipond-edge` and their named timers; keep protected
backups until explicitly retired. Do not remove shared firewall, IPsec, PBS or
mail configuration.

Before final cutover, verify real REST/Realtime traffic, both instances, worker
lease fencing, backup restoration, monitoring, final production allowlist and
canonical OAuth flow. Prepare the production runtime separately, retaining the
existing production session secret. Obtain explicit final approval before
changing www.koipond.live, provider callbacks, or moving live writes.
