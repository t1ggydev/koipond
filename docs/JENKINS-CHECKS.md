# Jenkins pull-request checks

The required status is `continuous-integration/jenkins/pr-head`, emitted by the
installed GitHub Branch Source plugin from actual Jenkins queue/build events.
It is not a synthetic success posted by an operator or a status copied from a
different commit. The repository ruleset is changed only after this integration
has produced a successful check for the current pull-request head.

`Koipond-PR` is a multibranch job which discovers origin pull requests and builds
their exact head revision. It does not discover forks or automatically deploy
branches. A two-minute indexing trigger provides updates while Jenkins remains
private; no public webhook ingress is required. Polling can delay the initial
pending status briefly after a push.

The GitHub credential is a dedicated expiring fine-grained token restricted to
t1ggydev/koipond: Contents Read, Pull requests Read and Commit statuses Read/write
plus automatic Metadata Read. Jenkins stores it encrypted in the multibranch
folder's credential store. No GitHub token is exported to shell build steps.
The one-time bootstrap reads a protected input, saves the credential, removes
that input and removes its init script after successful setup. Protected recovery
material remains in the controller's ignored Vault directory.

The Jenkinsfile checks out SCM first, obtains the exact commit SHA, then fetches
and verifies that same SHA on the existing Ansible controller. It never substitutes
the current preparation-branch tip for a pull-request revision. A controller-side
file lock serializes the shared build workspace; state and immutable image tags
are separated by Jenkins run identity. PR builds always skip private deployment,
even if someone selects its parameter. Production deployment is absent. The
read-only SCM checkout retries up to three times, with a delay after failures,
to tolerate short network interruptions without blindly repeating deployments.

All build steps run on the dedicated `koipond-build` agent. The existing Ansible
platform continues coordinating builds and isolated acceptance tests on apps01.
Container builds include lint, type/build validation, queue/RLS/concurrency and
two-instance tests. Test containers and temporary source are removed afterward.

The Jenkins controller and agent run Java 25. The built-in controller has zero
build executors. The agent's inbound WebSocket uses a pinned SSH tunnel from the
Ansible host to Jenkins; its HTTP endpoint is bound to loopback on the agent.
The dedicated tunnel account permits only forwarding to Jenkins' local port and
has no shell access. The agent secret is a private file, not a process argument.
Both the harmless agent smoke job and the full PR build have succeeded.

Jenkins UI CSP is enforced through its managed service configuration. Installed
plugins were compared with the official compatibility inventory. The remaining
known email-ext limitation concerns template preview/testing, not ordinary job
execution; this compatibility exception is recorded in infrastructure evidence.
No Jenkins users or authorization strategy were changed. Native application
monitoring adds only the verified official Prometheus plugin and its required
metrics dependency; the original plugins were not upgraded. The controller
was restarted in an idle window with encrypted configuration recovery material;
agent reconnection was verified afterward. Both JVMs use private temporary
directories on their root disks instead of the small /tmp tmpfs. Jenkins also
has a reserve-checked persistent 2GiB swap file with swappiness10. No VM RAM or
disk expansion was needed according to the measured host and guest reserves.

The private native metrics endpoint feeds the existing Prometheus/Grafana stack.
Its application dashboard covers the queue, agent, executors, builds, JVM/GC and
SCM indexing; alerts include failed builds and failed/stuck indexing. Build
parameters and per-build labels are disabled, and disk-usage scanning is disabled.

The existing manual Koipond preparation job remains available for explicit private
staging deployment. The existing homelab job retains its provisioning logic and
defaults; only its execution location and matching SSH key path are migrated.
Do not run that provisioning job merely as a smoke test: its defaults target an
existing monitoring VM. Agent connectivity is verified with a separate harmless
smoke job and the real Koipond acceptance build.

Keep the old Vercel deployment disabled for this preparation branch. Changing a
GitHub status requirement does not authorize a merge, production DNS switch,
Cloud write fence, database final sync or production-container activation.

Token rotation: replace only `koipond-github-status` in the Koipond-PR folder and
verify a real check before revoking the old token. A missing, expired or insufficient
token must leave the PR blocked; never post a successful status as a workaround.
