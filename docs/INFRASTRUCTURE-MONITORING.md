# Site, VM and application monitoring

The existing home Prometheus/Grafana/Loki installation remains central. Monitoring
does not create another stack on the root server or duplicate existing exporters.
The detailed infrastructure deployment lives on the Ansible controller in branch
prep/koipond-infrastructure; the public source patch and test reports are delivered
in the task outputs directory. Production traffic remains unchanged.

Grafana folders are Homelab and pve01. Each site has an overview and reusable VM
detail selector; existing overview/backup dashboard UIDs are preserved. Stopped
Debian templates are shown in the PVE inventory without fake guest metrics.

| Site | VMs | Application/container views |
| --- | --- | --- |
| Homelab | 101 Ansible,102 Jenkins,103 monitoring,104 PBS | Jenkins; Grafana/Prometheus/Loki; Uptime Kuma container; existing PBS/backup views |
| pve01 | 110 edge01,120 apps01,130 supabase01,140 mail01,150 PBS | Reverse proxy; two web/two worker containers; Supabase/PostgreSQL; Mailcow/MariaDB/Redis; existing PBS/backup views |

All nine running VMs have guest CPU/memory/filesystem monitoring. Guest memory
pressure uses MemAvailable rather than interpreting QEMU RSS/cache as allocated
memory pressure. Swap occupancy alone does not trigger a memory-pressure alarm.
The measured resource inventory found no need for VM RAM/disk expansion; the
Jenkins temporary-directory and swap adjustments are documented separately.

Docker views contain35 individual containers on five VMs. Their selectors group
by site, VM, Compose project, service and container. Real Docker counters provide
CPU, working-set memory, hard limits, health, restarts, OOM, traffic and disk I/O.
The local bounded collector reads the Unix socket and publishes through existing
node_exporter textfiles. No Docker TCP API or additional privileged monitoring
container is exposed. Missing counter support is marked unavailable, not zero.
Persistent expected-service manifests retain visibility when a durable container
is removed; temporary oneoffs are excluded. Maintenance must explicitly disable
the affected expectation or use a bounded alert silence.

Application details include PostgreSQL connections/limits, transaction/cache/lock/
deadlock/WAL/checkpoint statistics, eleven Supabase service states and four API
probes. pg_stat_statements is not active and is clearly marked unavailable; no
database restart was introduced to enable it. Mailcow retains its existing mail,
certificate, queue and backup checks, with added aggregated MariaDB and Redis
statistics. Queries are fixed read-only statistics and never expose SQL text,
mail contents, passwords or user identifiers as metric labels.

Jenkins uses its native private endpoint plus fixed read-only SCM/agent gauges.
The existing Homelab dashboard additionally contains native Grafana/Loki/Prometheus
process health. Site labels remain compatible with existing metrics; per-instance
Koipond scrapes carry apps01/VM120 labels and retain separate fixed backend paths.

Alarms cover outages, resource pressure, collector freshness, container health,
missing expected services, OOM/restart bursts, configured memory limits, database
problems, CI/build/indexing failures and backup health. Specific application down
alarms take precedence over equivalent generic container down alarms; derived
database failures are gated to avoid duplicate notifications. Promtool fixtures
test healthy, sustained failure, transient and explicit negative cases without
stopping live services. Grafana expression evaluation and existing Telegram
receiver bindings are verified. Bot credentials and the configured private chat
were checked read-only; no test message was sent and message receipt is not claimed.

Backup coverage intentionally consists of seven VM images plus configuration-
based appliance recovery for PBS VMs104/150, whose datastore disks have backup=0.
Datastore sync, host configuration freshness and restore-test evidence remain
visible. Fresh encrypted snapshots and incremental verification use the existing
jobs; no retention change or recursive NAS initialization is introduced.
