# Koipond self-hosting preparation

Approved scope: inventory and isolated preparation. Vercel, hosted Supabase,
production DNS and Twitch subscriptions remain unchanged. Production cutover
requires successful acceptance tests and Leon's explicit approval.

## Tasks

- [ ] Prepare reproducible container packaging and liveness checks (local draft).
- [ ] Complete read-only Vercel and Supabase inventory after scoped access is supplied.
- [ ] Add private readiness and aggregate monitoring instrumentation.
- [ ] Replace process-local EventSub deduplication with durable ingestion and a worker.
- [ ] Coordinate sessions, Server Actions, cache handling and two-instance deployments.
- [ ] Add distributed scheduling and protected statistics refresh execution.
- [ ] Prepare isolated Supabase staging and test-only Twitch configuration.
- [ ] Prepare Jenkins builds and explicitly gated Ansible staging deployment.
- [ ] Run functional, recovery, concurrency and load acceptance tests.
- [ ] Obtain explicit approval for the Vercel-to-pve01 production cutover.
- [ ] Observe the app migration before planning the hosted Supabase cutover.
- [ ] Obtain separate approval for the database cutover after restore tests.

## References

- Production baseline: d723d8d74ad6e3c51eed1797153f872581e2b34b.
- Vercel project: https://vercel.com/t1ggys-projects/koiproject
- Supabase project: https://supabase.com/dashboard/project/xlatvbaiwfftkxgtzrly
- Local branch: prep/koipond-self-hosting. No push or deployment has been performed.
