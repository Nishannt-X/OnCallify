# What broke while building this, and what it taught

Every entry happened during the build and is reproducible from the commit history.
These are the stories worth telling in an interview, because each one is a real
on-call lesson that the system now encodes.

## Diagnosis

**Latency is not a cause.** The first live connection-leak incident was diagnosed as
"FX provider degraded". The rule said: high transfer latency means the provider is
slow. But a leak makes every request wait the pool's `maxWait` (500 ms) before failing,
so latency was pinned at exactly 0.500 s. Fix: FX trouble must show up as FX timeout
errors; latency alone proves nothing. Lesson: symptoms shared by several causes cannot
discriminate between them. The safety model held: the wrong fix was only a proposal, and
a human rejected it.

**A leak looks different under load.** The textbook leak signature ("connections held
while request threads are idle") failed under steady traffic: waiting threads count as
busy, so held/busy was 0.86, not > 1.5. The diagnosis that works is by elimination: pool
exhausted, pool size unchanged (no config change), provider healthy, therefore the
connections are not being returned.

**Real services do not report their own leaks.** An early version exported a
`leaked_connections` gauge. That is cheating: no real service knows it is leaking. It was
removed, so the diagnosers have to infer the leak like a human would.

## Alerting and verification

**Which alert fires first is a race.** A standing contract was scoped to the incident's
"primary" (first) alert. On the repeat incident, `PoolSaturated` fired 2 s before
`InfraErrors`, the primary was different, and the contract did not match. Fix: a contract
matches if its rule is among the incident's alerts.

**Verify the page, not the ticket.** An incident escalated although the fix worked: the
verifier watched the first alert, `PoolSaturated`, which is legitimately at 100% when 12
workers share a 10-connection pool, and took 74 s to clear. Fix: only `page` alerts open
incidents (tickets attach as context), and recovery means every paging alert resolved after
the fix.

**A dead service reports nothing.** A fast memory leak drove the JVM into a GC death spiral
(even Tomcat's acceptor thread died with OutOfMemoryError). Scrapes timed out, there were no
metrics, so no rule could fire: silence looked like health. Fix: a failed scrape is itself a
sample (`scrape_failed=1`, the Prometheus `up` idea) and `LedgerlineDown` pages on it. The
fault was also slowed to a realistic gradual leak, so heap pressure is visible first.

## Runbooks and data

**A dry run must be the real run, rehearsed.** The revert runbook pre-checked with
`git apply --check`, which uses three lines of context; an unrelated edit three lines away
made it refuse a revert that `git revert` merges cleanly. Fix: rehearse the exact
`git revert` in a throwaway clone. The dry run now gives the answer the real run will.

**Half-written files.** `git revert` rewrites the config in place. If the app read it
mid-write and the parse failed, the old code remembered the file's timestamp and never
retried. Fix: a failed parse keeps the last good config and retries on the next check.

**String timestamps must be fixed width.** Python's `isoformat()` drops `.000000` when the
microseconds are zero, so `"...:00Z"` sorts after `"...:00.5Z"`. Expiry checks compared ISO
strings. Fix: always `timespec="microseconds"`.

## The experiment harness

**Evidence windows leak between scenarios.** With a 120 s look-back and scenarios ~70 s
apart, a bundle contained 210 errors from the previous run. Fix: 60 s look-back (alerts fire
~10 s after onset) and at least 50 s of clean traffic before every injection.

**Peeking is worse than it sounds, and exact tests hide it.** The first A/A simulation
showed naive peeking at only 4.5% false positives, because an exact McNemar test with 7 looks
is conservative. Measured honestly (z-test on a continuous metric, 50 looks): 30.7% false
positives; always-valid mSPRT p-values at the same looks: 2.9%.

## Environment

**`Asia/Calcutta` is not a valid PostgreSQL time zone.** The JVM inherited the laptop's
legacy zone alias and every connection failed. Servers run in UTC (`-Duser.timezone=UTC`).

**Tomcat auto-deploy races health checks.** Copying a WAR into `webapps/` and polling
`/api/health` can hit the *old* context before the redeploy starts. Deploys now go through
the synchronous manager API (`/manager/text/deploy`), and auto-deploy is off.
