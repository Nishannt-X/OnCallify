# OnCallify

**An on-call assistant for software engineers that is measured, not trusted.**

When a service pages, OnCallify reads the evidence (logs, metrics, and *what changed*),
diagnoses the cause, proposes **exactly one allowlisted fix** with its exact parameters,
dry-runs it, and waits for a human to approve it by typing the proposal's fingerprint.
It then executes the fix and refuses to call the incident resolved until the metrics agree.
A human can grant a **standing contract** so the same fault, next time, is fixed without
paging anyone.

Every diagnoser change (a new prompt, a different model, an ML classifier) is first
**A/B tested on replayed real incidents** with paired statistics, and CI blocks it unless it
is non-inferior on correct fixes and on unsafe actions.

```
 Ledgerline (Java 21 servlets, WAR on Tomcat 10, PostgreSQL)    <- the service that breaks
        | JSON logs + Prometheus metrics
        v
 collector (Python) --events--> gateway (Node/Express, MongoDB) -- alert rules --> agent (Python, FastAPI)
                                  | sticky experiment assignment                  | evidence -> diagnose -> plan
                                  | idempotent ingest                             | dry run -> proposal -> approval
                                                                                  | runbook (bash | PowerShell)
                                                                                  | verify (3 checks) -> postmortem
                                                                                  v
                                                       PostgreSQL (incidents, approvals, contracts, audit)
                                                       React + TypeScript dashboard
```

## What is in the repo

| Path | What | Stack |
|---|---|---|
| `ledgerline/` | the watched service: a payments ledger with idempotent transfers, FX conversion, a hot-reloading config, and real fault switches | Java 21, Jakarta Servlet 6, **Tomcat 10.1**, Tomcat JDBC pool, PostgreSQL, Jackson, JUnit 5 |
| `gateway/` | event ingest (deduplicated by event id), alert rule engine (pending → firing → resolved), experiment assignment and exposure logging | **Node 22, Express 5, TypeScript**, **MongoDB**, zod, Jest + supertest |
| `agent/` | the assistant: evidence, diagnosers, planner, approvals, contracts, runbook executor, verifier, postmortems, API, collector, experiments | **Python 3.12**, FastAPI, httpx, **PostgreSQL** (psycopg 3), MongoDB / **IBM Cloudant** transcripts, numpy/scipy/scikit-learn, pytest, mypy --strict |
| `runbooks/` | the only things that can change production, each written twice with one contract | **bash** (shellcheck, bats) and **PowerShell** (PSScriptAnalyzer, Pester) |
| `web/` | dashboard: incidents, evidence, the approval gate, contracts, experiments, safety | **React 18, TypeScript**, Vite, Vitest |
| `eval/` | the replay corpus captured from the live stack, and experiment reports | JSON bundles, Markdown/JSON reports |
| `deploy/` | local stack without Docker (Tomcat base, Postgres roles, config git repo), optional Compose | bash |
| `.github/workflows/ci.yml` | Java, Node, Python, shell, Windows PowerShell, web, a full-stack integration run, and the experiment gate | GitHub Actions |

## The safety model

Safety lives in code and tests, never in a prompt.

1. **Allowlist.** Five actions with exact, validated parameter schemas
   (`restart_app`, `revert_config_change`, `resize_pool`, `enable_fx_fallback`,
   `sync_db_credential`). Anything else is refused before a human ever sees it.
2. **Diagnosers do not choose actions.** They output a cause (and a suspect change). A
   shared, boring planner maps the cause to one action. A hallucinating model cannot
   invent a new action.
3. **Dry run by the real runbook,** with the same privileges as the real run, before the
   proposal exists. A config revert is rehearsed in a throwaway clone so the dry run gives
   the same answer the real run will.
4. **Approval is a record:** bound to the SHA-256 of `(action, params)`, typed as an
   8-character fingerprint (not "yes"), expiring, consumed **once** with an atomic
   conditional UPDATE (tested with eight racing database connections), and re-checked
   against the proposal at execution time.
5. **Contracts** cover one alert rule + cause + action + exact params, for limited days
   and uses, with a confidence floor. Actions whose params change every time (reverts)
   can never be contracted, because that would be a blank cheque.
6. **Verification needs three checks:** every paging alert resolved *after* the fix, no
   new infrastructure errors across two scrapes, and the action's own post-condition
   (the app restarted after the fix, the flag is on, the reverted keys are back, the
   credentials match).
7. **Kill switch** blocks execution even of approved actions. **Audit log** for every
   decision.

Design credit: the safety model follows [Beacon](https://github.com/Prashant-thakur77/Beacon)
(MIT). See `docs/ORIGIN.md` for exactly what was kept, changed and added.

## The experiments

`eval/corpus/` holds incidents **captured by really breaking the running stack** in seven
ways, eight variations each, some with red herrings (an unrelated config commit or an
unrelated secret rotation just before the fault):

| Fault family | How it is caused | Correct fix |
|---|---|---|
| connection leak | code path stops returning pooled connections | `restart_app` |
| pool undersized | a config commit shrinks `pool.max` / `pool.max.wait.ms` | `revert_config_change` (that commit) |
| bad config | a config commit points `fx.url` at a dead host or wrong path | `revert_config_change` (that commit) |
| FX provider degraded | provider becomes slow; calls time out while holding connections | `enable_fx_fallback` |
| credential drift | DB password rotated in the vault, app still uses the old one | `sync_db_credential` |
| memory leak | heap climbs until the JVM stalls | `restart_app` |
| transient | 8-12 s provider blip that clears on its own | none |

Every variant is replayed on the same incidents (**paired design**) and scored on: cause
correct, correct fix (including the right commit), **unsafe** (changes production but is not
a correct fix), and missed. Statistics in `agent/src/oncallify/eval/stats.py`:

* exact **McNemar** for paired binary outcomes; **cluster bootstrap** CIs and a **cluster
  sign-flip test** when a nondeterministic variant (an LLM) is repeated, because repeats of
  one incident are not independent;
* **Holm** correction across metrics; **power / minimum detectable effect**, so the report
  says what the corpus *cannot* detect instead of overclaiming;
* **SRM** check for the live split; **non-inferiority** bounds for the CI gate;
* **always-valid p-values (mSPRT)** for the live experiment, and an **A/A simulation**
  proving the tests reject at their nominal rate (and showing that naive peeking does not).

Variants: `rules-v1` (log keywords only), `rules-v2` (+ change ledger, metric shape,
elimination), `ml-logreg` (logistic regression on 34 named features, leave-one-group-out
cross-validated, weights stored as JSON), `granite` and `granite-rag` (IBM Granite via
Ollama, with structured outputs validated as untrusted input; RAG over past incidents that
never retrieves the incident being scored).

The latest report: [`eval/reports/report.md`](eval/reports/report.md).

## Run it

Needs: Java 21, Node 22, Python 3.11+, PostgreSQL 16, Tomcat 10.1 (`apt install tomcat10
tomcat10-admin` or Homebrew `tomcat`), git. No Docker required.

```bash
export PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres   # a superuser, for setup only
make setup        # deps, Postgres roles/databases, Tomcat base, config git repo, secrets
make build        # WAR (offline build.sh, or Maven), gateway, dashboard
make up           # Tomcat+Ledgerline :8080, gateway :7070, agent + dashboard :7080
oncallify approver --name you          # prints your approver token (stored hashed)
make demo TOKEN=<token>                # night 1: you approve; night 2: a contract fixes it
make test         # JUnit, Jest, pytest, bats, Vitest
make eval         # replay the corpus through rules-v1, rules-v2, ml-logreg
```

With [Ollama](https://ollama.com) and a Granite model (`ollama pull granite4:micro`;
set `ONCALLIFY_LLM_MODEL` to the tag you pulled), `make eval-llm` adds the Granite arms,
and `ONCALLIFY_VARIANT=granite make up` makes Granite the live diagnoser. Set
`ONCALLIFY_GUARDIAN_MODEL` to a Granite Guardian tag for an advisory second opinion on
every proposal (it can only add caution).

## Honest limitations

* One watched service, one host. The runbooks act on a local Tomcat and a local config
  repository, not on a fleet.
* The corpus is about 56 incidents. The report states the minimum detectable effect; a
  difference smaller than that is not claimed.
* The Granite arms and the Cloudant transcript store are implemented and unit-tested
  against mocked HTTP; their numbers depend on the machine and model they are run with.
* The approver authentication is a hashed bearer token, enough for a team tool on
  localhost, not an identity provider.

MIT licensed. See `LICENSE` and `docs/ORIGIN.md`.
