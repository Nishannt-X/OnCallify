# OnCallify

**An autonomous on-call reliability agent with cryptographic safety gates and paired statistical experimentation.**

OnCallify investigates service outages, diagnoses root causes from logs, metrics, and change ledgers, and proposes exactly one allowlisted remediation. Every mutation requires explicit human approval via an 8-character cryptographic fingerprint—or an explicit, scoped standing contract. No remediation is marked recovered until real post-fix health checks and metric baselines verify stability.

Diagnoser improvements (heuristics, ML models, or LLMs) are benchmarked against real replayed outages using paired statistical hypothesis testing (McNemar, Holm-Bonferroni, mSPRT), ensuring that changes merge only when proven non-inferior in accuracy and safe in production.

```
+-------------------------------------------------------------------------------+
|  Ledgerline (Java 21, Tomcat 10.1, PostgreSQL 16) - Watched Service           |
|  * Idempotent payment transfers, dynamic config reload, real fault switches   |
+-------------------------------------------------------------------------------+
       | JSON structured logs + Prometheus metrics
       v
+-------------------------------------------------------------------------------+
|  Gateway (Node 22, Express 5, TypeScript, MongoDB)                            |
|  * Idempotent event ingest & deduplication                                    |
|  * Stateful alert rule engine (pending -> firing -> resolved)                 |
|  * Sticky experiment variant assignment & exposure logging                    |
+-------------------------------------------------------------------------------+
       | Alert dispatch
       v
+-------------------------------------------------------------------------------+
|  Agent (Python 3.12, FastAPI, PostgreSQL, MongoDB / IBM Cloudant)             |
|  1. Evidence Collector (logs, metric windows, git config history, vault diff) |
|  2. Diagnoser (rules-v1, rules-v2, ml-logreg, IBM Granite via Ollama/watsonx) |
|  3. Deterministic Planner (maps diagnosed cause to 1 allowlisted action)      |
|  4. Runbook Dry-Run (executed in isolated clone with identical privileges)   |
|  5. Cryptographic Gate (8-char SHA-256 fingerprint approval / Sleep Contract) |
|  6. Runbook Execution (Bash & PowerShell runbooks)                            |
|  7. Three-Point Verifier & Automated Postmortem Generation                    |
|  8. IBM Granite Copilot (Grounded SRE telemetry assistant)                    |
+-------------------------------------------------------------------------------+
       |
       v
+-------------------------------------------------------------------------------+
|  Dashboard (React 18, TypeScript, Vite) & Immutable PostgreSQL Audit Log      |
+-------------------------------------------------------------------------------+
```

---

## Architecture & What is in the Repo

| Directory | Subsystem | Stack & Highlights |
|---|---|---|
| [`ledgerline/`](ledgerline/) | Monitored Service | Java 21, Jakarta Servlet 6, Tomcat 10.1, Tomcat JDBC pool, PostgreSQL 16. Real externally injectable fault switches. |
| [`gateway/`](gateway/) | Alert Gateway & Router | Node 22, Express 5, TypeScript, MongoDB, Zod. Deduplicated event ingestion, stateful alert lifecycle, sticky variant routing. |
| [`agent/`](agent/) | Remediation Agent | Python 3.12, FastAPI, PostgreSQL (psycopg 3), scikit-learn, Ollama / watsonx integration, Granite Copilot chat. |
| [`runbooks/`](runbooks/) | Dual Runbook Library | POSIX Bash (ShellCheck, Bats) and PowerShell 7 (PSScriptAnalyzer, Pester). Exactly identical execution contracts. |
| [`web/`](web/) | SRE Control Dashboard | React 18, TypeScript, Vite. Incident inspection, fingerprint approval panel, standing contract manager, live Copilot. |
| [`eval/`](eval/) | Evaluation Corpus & Stats | 56 real incident replays captured from live stack, paired statistical analysis engine, CI regression gates. |
| [`deploy/`](deploy/) | Deployment & Local Stacks | Native local setup scripts, configuration Git repo, vault simulator, optional Docker Compose environment. |
| [`.github/workflows/`](.github/workflows/) | CI/CD Pipelines | Multi-runtime test matrix (Java, Node, Python, Shell, PowerShell, React) and automated statistical non-inferiority gate. |

---

## Cryptographic Safety Model

Safety in OnCallify is enforced deterministically in code and database constraints, never delegated to model prompts:

1. **Strict 5-Action Allowlist**:
   Only five actions can ever touch infrastructure:
   - `restart_app`: Controlled Tomcat service recycle.
   - `revert_config_change`: Git revert of a breaking configuration commit.
   - `resize_pool`: JDBC connection pool resize.
   - `enable_fx_fallback`: Circuit breaker fallback activation for foreign exchange services.
   - `sync_db_credential`: Database password synchronization against the secure credential vault.
   Any parameter schema mismatch or unlisted action is immediately rejected before reaching an operator.

2. **Diagnoser / Planner Separation**:
   Diagnosers (heuristics, ML models, or LLMs) output strictly an identified root cause and suspect entity. Diagnosers cannot choose or parameterize actions. A deterministic, rule-based planner maps the cause to exactly one allowlisted action. Hallucinating models cannot invent or execute arbitrary commands.

3. **Rehearsed Dry Runs**:
   Every proposed action is dry-run using the exact runbook with the same runtime permissions before creating a proposal. Config reverts are rehearsed inside a throwaway Git clone to ensure the revert applies cleanly before requesting authorization.

4. **8-Character SHA-256 Fingerprint Approvals**:
   Approvals cannot be granted with simple "yes" confirmations. Operators must confirm the proposal by entering an 8-character fingerprint derived from `SHA-256(action, params)`. Tokens expire after 15 minutes, are single-use, and are consumed atomically in PostgreSQL (`UPDATE ... WHERE consumed_at IS NULL`)—verified with race-condition tests across 8 concurrent worker threads.

5. **Standing "Sleep" Contracts**:
   For recurring, low-risk incidents, operators can grant scoped contracts binding an exact alert rule, cause, action, and parameters with an execution quota, confidence threshold, and strict TTL. Dynamic actions whose parameters change per incident (such as commit reverts) cannot be contracted, preventing "blank check" executions.

6. **Three-Point Verification**:
   No incident is marked resolved upon command completion alone. Verification requires:
   1. The firing alert transitions to resolved in the gateway *after* fix completion.
   2. Zero new infrastructure error spikes across two consecutive scrape intervals.
   3. The action's specific post-condition passes (JVM restart verification, config commit restored, credentials verified).

7. **Kill Switch & Immutable Audit Trail**:
   A global kill switch halts all automated and approved execution immediately. All evidence, proposals, authorizations, and execution outputs are appended to an immutable PostgreSQL audit ledger.

---

## Evaluation & Statistical Rigor

Rather than evaluating on synthetic benchmarks, OnCallify is evaluated against **56 real incidents captured from the live stack** across 7 distinct fault families (8 variations each, including red-herring noise such as unrelated commits or secret rotations):

| Fault Family | Outage Mechanism | Expected Valid Fix |
|---|---|---|
| `connection_leak` | Code path leaks borrowed JDBC connections | `restart_app` |
| `pool_undersized` | Bad commit shrinks `pool.max` below workload demand | `revert_config_change` (breaking commit) |
| `bad_config` | Commit directs upstream FX provider to dead endpoint | `revert_config_change` (breaking commit) |
| `fx_degraded` | Upstream provider latency induces pool starvation | `enable_fx_fallback` |
| `credential_drift`| Vault secret rotated without updating app config | `sync_db_credential` |
| `memory_leak` | JVM heap exhaustion stalls execution | `restart_app` |
| `transient` | Temporary 8-12s network flap that clears automatically | No action |

### Statistical Methodology

Evaluations use a **paired replay design** in `agent/src/oncallify/eval/stats.py`:
- **Exact McNemar Test**: Matched-pairs test for binary outcomes (cause correct, unsafe action) across candidate and baseline variants on identical incidents.
- **Holm-Bonferroni Correction**: Family-wise error rate control across all evaluated metrics.
- **Cluster Bootstrap & Sign-Flip Tests**: Applied when evaluating nondeterministic LLM variants over repeated runs to account for within-incident correlation.
- **Statistical Power & MDE**: Power calculations document the Minimum Detectable Effect (MDE). With 56 paired incidents, differences smaller than ~21% cannot be reliably detected at 80% power ($\alpha=0.05$).
- **mSPRT (Mixture Sequential Probability Ratio Test)**: Always-valid p-values for live streaming experiment analysis, preventing false positives from continuous peeking.

### Verified Benchmark Results

Replaying the 56 captured incidents yields the following measured results (see [`eval/reports/report.md`](eval/reports/report.md)):

| Variant | Scenarios | Cause Accuracy | Action Correct | Unsafe Action Rate | Missed | Latency (p50) |
|---|---|---|---|---|---|---|
| `rules-v1` (baseline log keywords) | 56 | 67.9% | 69.6% | 16.1% | 14.3% | 0.00 ms |
| `rules-v2` (+ change ledger, metrics) | 56 | **100.0%** | **73.2%** | **0.0%** | 26.8% | 0.01 ms |
| `ml-logreg` (34-feature logistic reg) | 56 | **100.0%** | **73.2%** | **0.0%** | 26.8% | 0.04 ms |

- **rules-v2 vs rules-v1**: $+32.1\%$ cause accuracy ($p = 0.0000$, Holm-adjusted), $-16.1\%$ unsafe actions ($p = 0.0117$, Holm-adjusted).
- **Zero Unsafe Action Rate**: Both `rules-v2` and `ml-logreg` achieved 0.0% unsafe actions by refusing to propose unverified actions during ambiguous transient events.

---

## Setup & Quickstart

### Prerequisites
- **Java 21** & **Tomcat 10.1** (`brew install tomcat` or `apt install tomcat10 tomcat10-admin`)
- **Node.js 22** & npm
- **Python 3.11+**
- **PostgreSQL 16**
- **Git**

### Option A: Native Local Stack (Recommended for Development)

```bash
# 1. Provide superuser PostgreSQL connection for initial database & role provisioning
export PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres

# 2. Provision local directories, databases, config git repository, and credentials
make setup

# 3. Build Ledgerline WAR, Node gateway, and React dashboard
make build

# 4. Start all services:
#    - Tomcat + Ledgerline: http://127.0.0.1:8080
#    - Alert Gateway:       http://127.0.0.1:7070
#    - Agent & Dashboard:   http://127.0.0.1:7080
make up

# 5. Generate your approver authorization token
oncallify approver --name alice

# 6. Run the end-to-end interactive demo
make demo TOKEN=<your-approver-token>

# 7. Run full test suite & statistical evaluation
make test
make eval
```

### Option B: Docker Setup

```bash
# 1. Initialize container runtime secrets and tomcat user configuration
./deploy/docker-setup.sh

# 2. Start PostgreSQL, MongoDB, Tomcat, and the Gateway via Compose
docker compose -f deploy/docker-compose.yml up
```

### Optional: IBM Granite & Ollama Integration

To enable conversational copilot assistance and local LLM diagnosers:
```bash
# Pull IBM Granite
ollama pull granite4:micro

# Run with Granite active as diagnoser
ONCALLIFY_LLM_MODEL=granite4:micro ONCALLIFY_VARIANT=granite make up

# Run paired evaluation including LLM arms
make eval-llm
```

---

## Honest Limitations

- **Single Service / Single Host Scope**: Runbooks currently target a single Apache Tomcat instance and a local Git configuration repository. Fleet-wide multi-cluster coordination is out of scope.
- **Corpus Statistical Power**: The evaluation dataset contains 56 real incidents. Differences smaller than ~21% cannot be statistically distinguished at 80% power.
- **Approver Authentication**: Approver tokens use salted SHA-256 hashes suited for localhost and development teams, rather than SAML/OIDC enterprise directory federation.
- **Hardware-Dependent Model Performance**: Latencies and inference throughput for LLM diagnosers (Granite) vary based on host GPU/CPU hardware. Offline evaluations mock HTTP endpoints for deterministic CI testing.

---

## License & Notices

OnCallify is distributed under the [MIT License](LICENSE).  
Third-party copyright notices and architectural lineage are preserved in [LICENSE-BEACON](LICENSE-BEACON) and [docs/ORIGIN.md](docs/ORIGIN.md).
