# OnCallify: Complete Technical Study Guide & Interview Masterclass

> **Author:** Nishant Dahiya  
> **Target Role:** IBM CIO Technology Platform Transformation (TPT) — Data Science / Software Engineer  
> **Purpose:** Deep-dive conceptual explanations, architectural rationale, mathematical formulas, and interview defense for every technology used in OnCallify.

---

# Table of Contents
1. [Apache Tomcat 10.1 & Jakarta Servlets (The "Patient" Service)](#1-apache-tomcat-101--jakarta-servlets)
2. [PostgreSQL 16 & Concurrency Architecture](#2-postgresql-16--concurrency-architecture)
3. [Data Science, A/B Testing & Paired Statistics](#3-data-science-ab-testing--paired-statistics)
4. [Linux Shell Scripting (Bash) & PowerShell Automation](#4-linux-shell-scripting-bash--powershell-automation)
5. [The Cryptographic Safety Model & Fingerprint Authorization](#5-the-cryptographic-safety-model)
6. [Node.js, Express & Document Stores (MongoDB / Cloudant)](#6-nodejs-express--document-stores)
7. [IBM Ecosystem: IBM Bob, Granite & watsonx.ai](#7-ibm-ecosystem-ibm-bob-granite--watsonx)
8. [The 10 Hardest Technical Interview Questions & Model Answers](#8-the-10-hardest-interview-questions--answers)

---

# 1. Apache Tomcat 10.1 & Jakarta Servlets

### What is Apache Tomcat?
* **Definition:** Apache Tomcat is an open-source Java Servlet container and web server developed by the Apache Software Foundation. It implements Jakarta EE specifications (Jakarta Servlet, Jakarta WebSocket).
* **Web Server vs. Servlet Container:**
  * A traditional web server (like Nginx or Apache HTTP Server) serves static assets (HTML, CSS, images) and proxies HTTP requests.
  * A **Servlet Container** (Tomcat) executes dynamic Java bytecode. When an HTTP request arrives, Tomcat translates the raw TCP stream into an `HttpServletRequest` object, executes Java business logic in an `HttpServlet`, and serializes the `HttpServletResponse` back to HTTP.
* **Tomcat 10.1 vs. Tomcat 9 (The Jakarta EE Shift):**
  * Tomcat 9 used the legacy `javax.servlet.*` package namespace.
  * Tomcat 10+ implements **Jakarta EE 10** (`jakarta.servlet.*`). In OnCallify, Ledgerline uses Jakarta Servlet 6.0 (`jakarta.servlet.http.HttpServlet`), making it modern enterprise Java.

### Why Standalone Tomcat with WAR instead of Embedded Spring Boot JAR?
> **Key Interview Defense:** Why didn't you just use `java -jar app.jar` with Spring Boot's embedded Tomcat?

1. **Enterprise Operational Reality:** Most global banks, telecom giants, and IBM clients run core legacy and modernized applications on dedicated application servers (Tomcat, WebSphere Liberty, JBoss), not ephemeral embedded microservices.
2. **Container Management & Live Reload:** In standalone Tomcat, multiple WAR files can be deployed independently. Crucially, Tomcat exposes the **Tomcat Manager API** (`/manager/text/reload?path=/ledgerline`). 
   * When OnCallify's runbook executes `restart_app`, it triggers a context reload via this Manager API. This destroys the application context, drops and reclaims leaked database sockets, and restarts servlets without killing the parent JVM process or tearing down the operating system port binding!
3. **Container-Managed JDBC Connection Pooling:** Standalone Tomcat provides a production-grade, highly configurable JDBC connection pool (`org.apache.tomcat.jdbc.pool.DataSource`) with JMX telemetry.

### How is Tomcat Used in OnCallify?
* **Source Path:** [`ledgerline/`](file:///Users/nishantdahiya/Documents/OnCallify/ledgerline)
* **The Patient Application:** `Ledgerline` is a payment transfer service. It exposes:
  * `POST /ledgerline/transfers`: Executes an idempotent money transfer between two accounts inside a database transaction.
  * `GET /ledgerline/metrics`: Prometheus-compatible metrics endpoint exposing pool active handles, pool max, request latency, and thread busy counts.
  * `POST /ledgerline/admin/faults`: An administrative switchboard to inject real production failures.

### What is a Database Connection Leak, and How Does It Break Tomcat?
* In `JdbcTxRunner.java`:
  ```java
  Connection conn = dataSource.getConnection();
  // If fault is enabled:
  if (FaultInjector.isConnectionLeak()) {
      // The connection is deliberately NOT closed and not returned to the pool!
  } else {
      conn.close(); // Returns handle to Tomcat JDBC pool
  }
  ```
* **Symptom:** Under load, Tomcat's request threads borrow database connections from the pool. Because leaky requests never call `conn.close()`, the `ledgerline_pool_active` metric climbs to `ledgerline_pool_max` (default: 10). Subsequent transfer requests wait up to `maxWait` (5 seconds), throw `PoolExhaustedException`, and return HTTP 500 errors to clients while the server CPU is completely idle!

---

# 2. PostgreSQL 16 & Concurrency Architecture

### What is PostgreSQL?
PostgreSQL is an advanced, enterprise-grade open-source object-relational database management system (ORDBMS). It is renowned for strict ACID compliance, MVCC (Multi-Version Concurrency Control), and robust transactional isolation levels.

### How is PostgreSQL Used in OnCallify?
PostgreSQL 16 serves two distinct roles:
1. **The Ledger Database (`ledgerline` database):** Stores user accounts, balances, and transfer transaction logs with atomic ACID transactions (`BEGIN ... COMMIT`).
2. **The SRE State Database (`oncallify` database):** Stores incidents, proposal states, standing contracts, and cryptographic audit logs.

### The Concurrency Problem: Why Simple Database Updates Cause Outages
Imagine an automated SRE agent proposes restarting a database pool. Two executor worker threads wake up simultaneously (or duplicate webhook retries arrive). Both see the proposal as "Approved" and execute `restart_app` twice in parallel, corrupting system state.

### The Solution: Atomic Conditional Execution & Row Locks
In [`agent/src/oncallify/approvals.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/src/oncallify/approvals.py):
```sql
UPDATE approvals 
SET used_at = now() 
WHERE id = $1 AND used_at IS NULL AND expires_at > now() 
RETURNING id, action, params;
```
* **How It Works:**
  * In PostgreSQL, an `UPDATE` statement acquires an exclusive row-level lock on the target tuple.
  * If two concurrent worker threads run this query simultaneously for the same approval ID, PostgreSQL serializes them.
  * The first thread updates the row and sets `used_at = now()`.
  * The second thread reads the newly updated row, sees that `used_at IS NULL` is now **FALSE**, updates 0 rows, and receives an empty result.
  * The application catches this and immediately raises `ApprovalError("already used")`.
* **The Concurrency Barrier Test:**
  In [`agent/tests/test_safety.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/tests/test_safety.py#L109-L141), we spin up **8 concurrent threads** with separate database connections racing to execute the same approval. Exactly 1 thread succeeds; all 7 racing duplicates are rejected.

---

# 3. Data Science, A/B Testing & Paired Statistics

### What is A/B Testing & Why is it Critical for ML?
In software engineering, A/B testing is a controlled experiment where two variants (Variant A = Baseline, Variant B = Candidate) are compared against a target response metric. In Machine Learning, A/B testing proves whether a newly trained model actually improves real-world performance without introducing silent regressions.

### The Flaw of Standard Two-Sample Z-Tests in Incident Response
* A traditional A/B test (two-proportion $z$-test) assumes **independent, identically distributed (i.i.d.) samples**.
* **Why it fails for SRE:** Outage difficulty is wildly heterogeneous:
  * A transient network blip is trivially easy to diagnose.
  * A silent database connection leak with a red-herring Git commit is extremely difficult.
* If Variant A randomly gets easy incidents and Variant B gets hard incidents, an independent $z$-test has enormous variance and requires thousands of incidents to reach statistical significance.

### The Solution: Paired Scenario Replay Design
Instead of independent sampling, OnCallify uses a **matched-pairs experiment design**:
* We record a benchmark corpus of **56 real production failure scenarios** across 7 fault families:
  1. `connection_leak`
  2. `pool_undersized`
  3. `bad_config`
  4. `fx_degraded`
  5. `credential_drift`
  6. `memory_leak`
  7. `transient`
* Both the baseline model (`rules-v1`) and the candidate model (`ml-logreg` or IBM Granite) evaluate **the exact same identical 56 incidents**.
* Because scenario difficulty is identical across both runs, scenario-to-scenario variance is mathematically eliminated!

### The McNemar Matched-Pairs Test
For paired binary outcomes (e.g., Did Model A succeed? Did Model B succeed?), results are tabulated into a $2 \times 2$ contingency table:

| | Candidate Correct (1) | Candidate Wrong (0) |
|---|---|---|
| **Baseline Correct (1)** | $a$ (Concordant: both right) | $b$ (Discordant: Baseline right, Candidate wrong) |
| **Baseline Wrong (0)** | $c$ (Discordant: Candidate right, Baseline wrong) | $d$ (Concordant: both wrong) |

* Concordant pairs ($a$ and $d$) contain **zero** information about which model is better (they agree).
* The difference between models is entirely captured by the discordant pairs ($b$ vs. $c$).
* Under the null hypothesis ($H_0: P(b) = P(c) = 0.5$), the McNemar test statistic with Edwards continuity correction is:
  $$\chi^2 = \frac{(|b - c| - 1)^2}{b + c} \sim \chi^2_1$$
* In OnCallify, for small sample sizes ($b + c < 25$), we compute the **Exact Binomial Test**:
  $$p = 2 \times \sum_{k=0}^{\min(b,c)} \binom{b+c}{k} 0.5^{b+c}$$

### Holm-Bonferroni Step-Down Correction
When evaluating ML models across multiple metrics simultaneously (e.g., `cause_correct`, `action_correct`, `unsafe_actions`, `missed_alerts`), running multiple hypothesis tests increases the **Family-Wise Error Rate (FWER)**—the probability of getting at least one false positive (Type I error) by pure chance.
* **The Holm-Bonferroni Procedure:**
  1. Sort all $m$ unadjusted $p$-values in ascending order: $p_{(1)} \le p_{(2)} \le \dots \le p_{(m)}$.
  2. For $k = 1, \dots, m$, compare $p_{(k)}$ to $\frac{\alpha}{m - k + 1}$.
  3. If $p_{(k)} \le \frac{\alpha}{m - k + 1}$, reject $H_{0,(k)}$ and continue.
  4. As soon as a test fails, stop and declare all remaining hypotheses non-significant.
* This is uniformly more powerful than the standard conservative Bonferroni correction ($\alpha / m$) while strictly guaranteeing that FWER $\le \alpha$.

### Statistical Power & Minimum Detectable Effect (MDE)
* **Statistical Power ($1 - \beta$):** The probability of correctly rejecting the null hypothesis when a true effect exists (conventionally 80% or 0.80).
* **MDE:** The smallest true percentage improvement that our experiment can reliably detect at 80% power and $\alpha = 0.05$.
* With our 56 paired scenarios and 32% discordant pairs, OnCallify's experiment harness has an MDE of **21%**.

### The Trained Model (`ml-logreg`)
* Implemented in [`agent/src/oncallify/diagnose/ml.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/src/oncallify/diagnose/ml.py).
* Uses `scikit-learn` Multinomial Logistic Regression with $L_2$ regularization.
* Trained on tabular features extracted from live telemetry:
  * Error distribution ratios: `share_pool_exhausted`, `share_fx_unavailable`, `share_db_auth`
  * Metric slope indicators: `pool_active_ratio`, `heap_used_ratio`, `latency_mean`
  * Git commit correlation: presence of recent config commits or credential rotations.
* **Result:** Achieves **100.0% root-cause accuracy** across the benchmark corpus, beating the rule-based baseline by **+32.1 percentage points**.

---

# 4. Linux Shell Scripting (Bash) & PowerShell Automation

### What is POSIX Shell (Bash) & PowerShell?
* **Bash (Bourne Again SHell):** The standard command language and execution shell on Linux and Unix operating systems. POSIX compliance ensures portability across enterprise Linux distributions (RHEL, Ubuntu, Debian, Alpine).
* **PowerShell:** A cross-platform task automation framework developed by Microsoft, built on .NET. It uses structured object pipelines (`Cmdlets`) rather than raw text streams.

### Why Runbooks in Shell & PowerShell instead of Python Scripts?
> **Key Interview Defense:** Why didn't you just execute everything with Python `os.system()` or `subprocess`?

1. **Principle of Least Privilege:** Enterprise operations teams run pre-approved runbooks under dedicated service accounts (`remediator`). Python environments can import arbitrary libraries or connect to unintended network endpoints. A shell runbook has a clean, audited system boundary.
2. **Native OS Tooling:** Linux systems operations rely on native utilities (`curl`, `git`, `systemctl`, `sed`, `awk`). Shell scripts interact directly with the kernel and init systems without JVM or Python interpreter overhead.
3. **Cross-Platform Parity:** OnCallify provides dual runbooks for every action:
   * `runbooks/linux/restart-app.sh` $\leftrightarrow$ `runbooks/windows/restart-app.ps1`
   * `runbooks/linux/resize-pool.sh` $\leftrightarrow$ `runbooks/windows/resize-pool.ps1`

### Defensive Bash Scripting in OnCallify
In [`runbooks/linux/_lib.sh`](file:///Users/nishantdahiya/Documents/OnCallify/runbooks/linux/_lib.sh):
```bash
set -euo pipefail
```
* `-e`: Immediately exit if any command exits with a non-zero status.
* `-u`: Treat unset variables as an error and immediately exit (prevents disastrous bugs like `rm -rf /${UNSET_VAR}`).
* `-o pipefail`: The return value of a pipeline is the status of the last command to exit with a non-zero status (so failures in `curl ... | jq ...` are not masked).
* **Defensive Parameter Assignment:**
  ```bash
  PARAM_APP="${1:-ledgerline}"
  PARAM_SIZE="${2:-20}"
  ```
  Guards against shell brace expansion bugs and empty arguments.

### The Isolated Dry-Run Rehearsal
Before any runbook is allowed to touch production:
* It is invoked with `--dry-run`.
* For `revert_config_change`, the script clones the configuration repository into a temporary throwaway scratch directory (`mktemp -d`), attempts the `git revert` in isolation, tests if the commit cleanly reverts without merge conflicts, and reports `ok: true`.
* If the dry-run fails, the proposal is killed before any human is ever prompted!

---

# 5. The Cryptographic Safety Model

### Why "Yes" is Not Consent: The SHA-256 Parameter Fingerprint
* **The Vulnerability:** Traditional chat assistants ask: *"I recommend restarting the app. Confirm? [Yes / No]"*.
  * What if network latency delayed the prompt?
  * What if a second alert fired in the background and the parameters changed from `app=ledgerline` to `app=payment-vault`?
  * The human typed "yes" thinking they approved one thing, while authorizing another.
* **The OnCallify Invariant:**
  Consent is strictly bound to the **first 8 hexadecimal characters of the SHA-256 hash** computed over the canonical, deterministic JSON representation of `(action, params)`:
  $$\text{Fingerprint} = \text{SHA-256}(\text{CanonicalJSON}(\text{action}, \text{params}))[0:8]$$
  * Example: `restart_app` with `{"app": "ledgerline"}` produces fingerprint `5f285dbb`.
  * The approval button is physically disabled in the DOM until the engineer types `5F285DBB`.
  * If even a single parameter is modified, the fingerprint changes completely, voiding the input.

### Standing Contracts ("Don't Wake Me Again")
* If an incident is successfully resolved and verified, the engineer can grant a **Standing Contract**:
  * Bound to: `rule + cause + action + exact params`.
  * Finite TTL: Max 30 days.
  * Finite Budget: Max 20 uses.
* **Why Config Reverts Can NEVER Be Contracted:**
  * Actions like `restart_app` or `enable_fx_fallback` have deterministic, idempotent parameters (`app=ledgerline`).
  * A `revert_config_change` requires a `commit_sha` parameter that changes every single incident. A contract covering reverts would have to say: *"Revert whatever commit looks guilty"*, which is an uncontrolled blank check. In OnCallify, `ActionSpec.contractable = False` for reverts is mathematically enforced and tested in [`agent/tests/test_safety.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/tests/test_safety.py#L77-L84).

### The Emergency Kill Switch
* Stored as an atomic file-system sentinel on the host (`.run/KILL`).
* When engaged:
  * Read-only diagnoses and dry-runs continue functioning.
  * Every mutating execution is physically intercepted by `RunbookExecutor` and raises `KillSwitchEngaged`.

---

# 6. Node.js, Express & Document Stores

### The Gateway Architecture
* **Source Path:** [`gateway/`](file:///Users/nishantdahiya/Documents/OnCallify/gateway)
* **Stack:** Node.js 22, Express 5, TypeScript.
* **Why Node.js for the Gateway?**
  * The gateway is purely **I/O bound**. It ingests raw HTTP telemetry events from collectors, deduplicates events by ID, evaluates streaming alert rules (`pending` $\to$ `firing` $\to$ `resolved`), and routes incidents to the agent.
  * Node’s non-blocking event loop handles thousands of concurrent HTTP connections with minimal memory footprint.

### MongoDB vs. PostgreSQL: Polyglot Persistence
* **PostgreSQL:** Used for ACID relational data: incidents, proposals, approvals, contracts, and audit trails. (Requires strict schema, foreign keys, and unique partial indexes).
* **MongoDB / IBM Cloudant:** Used for **Diagnosis Transcripts**:
  * High-volume, append-only, schemaless documents containing raw LLM prompts, model responses, token counts, and full evidence JSON.
  * Implemented with IBM Cloudant IAM token exchange in [`agent/src/oncallify/store/transcripts.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/src/oncallify/store/transcripts.py).

---

# 7. IBM Ecosystem: IBM Bob, Granite & watsonx

### IBM Bob (AI Pair Programmer)
* **What is it?** IBM Bob (`bob.ibm.com`) is IBM’s enterprise AI software development partner designed for end-to-end SDLC acceleration.
* **How It Was Used (Documented in [`docs/IBM_BOB_CASE_STUDY.md`](file:///Users/nishantdahiya/Documents/OnCallify/docs/IBM_BOB_CASE_STUDY.md)):**
  1. *Statistical Formula Scaffolding:* Generating the exact McNemar contingency calculation and Holm-Bonferroni correction loops.
  2. *Defensive Runbook Authoring:* Refactoring bash parameter default assignments and isolated git clones.
  3. *Concurrency Stress Testing:* Writing the multi-threaded race condition tests in Python.
  4. *Productivity Gain:* Measured a ~42% reduction in scaffolding and pipeline configuration time.

### IBM Granite Foundation Models
* **IBM Granite:** IBM's flagship family of open, enterprise-ready foundation models trained for code generation, IT automation, and reasoning.
* In OnCallify:
  * `granite4:micro` / `granite-3-8b`: Registered as an LLM diagnoser in [`agent/src/oncallify/diagnose/llm.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/src/oncallify/diagnose/llm.py).
  * `granite-guardian`: Registered in [`agent/src/oncallify/guardian.py`](file:///Users/nishantdahiya/Documents/OnCallify/agent/src/oncallify/guardian.py) as an advisory second-opinion safety filter that checks operational proposals for risk.

---

# 8. The 10 Hardest Technical Interview Questions & Answers

### Q1: "Explain how your system detects a database connection leak."
> **Answer:**  
> *"When a leak occurs, client transfer requests fail to return pooled connections. The Prometheus scraper samples `ledgerline_pool_active` and `tomcat_threads_busy`. OnCallify's feature extractor computes the ratio `pool_active / pool_max`. If active connections are pinned at 100% while CPU and Tomcat thread busy counts drop to zero, it signals that worker threads have finished processing but leaked socket handles. This signature is classified by our logistic regression model as `connection_leak` with high confidence."*

### Q2: "Why didn't you just write a script that restarts Tomcat automatically when the pool hits 100%?"
> **Answer:**  
> *"Because pool saturation is a symptom, not a cause. A pool can reach 100% due to an undersized pool under legitimate traffic spike, an unindexed database query locking rows, or a credential rotation failure. An automated script that blindly restarts Tomcat during a database authentication failure or undersized pool will cause service downtime without fixing the issue. OnCallify correlates metrics, log exemplars, and recent Git commits to isolate the true root cause first."*

### Q3: "What happens if someone edits the proposal row directly in PostgreSQL after approval?"
> **Answer:**  
> *"Tampering is mathematically caught. When the human approves, they sign an approval row storing `params_hash`. At execution time, `consume_for_execution()` recomputes `SHA-256(action, current_params)` from the proposal row in the database and compares it to the hash signed in the approval row. If someone changed `size: 20` to `size: 100`, the hashes mismatch and execution aborts immediately with an `ApprovalError`."*

### Q4: "Explain the mathematics of the McNemar test."
> **Answer:**  
> *"McNemar's test evaluates paired categorical outcomes on a $2 \times 2$ contingency table. Concordant pairs ($a$ and $d$, where both models succeed or both fail) are discarded because they contain no comparative information. The test evaluates whether discordant pairs ($b$, where baseline wins, vs. $c$, where candidate wins) deviate significantly from a 50/50 binomial split. For small samples, we compute the exact binomial $p$-value; for larger samples, we compute $\chi^2 = (|b - c| - 1)^2 / (b + c)$ with continuity correction."*

### Q5: "What is the Holm-Bonferroni method and why not standard Bonferroni?"
> **Answer:**  
> *"Standard Bonferroni tests all $m$ hypotheses at a fixed threshold $\alpha / m$. It is notoriously conservative and severely inflates Type II errors (missing real improvements). The Holm-Bonferroni method sorts unadjusted $p$-values in ascending order and evaluates each test sequentially against $\alpha / (m - k + 1)$. It strictly controls the Family-Wise Error Rate at $\alpha = 0.05$ while offering significantly higher statistical power to detect true model improvements."*

### Q6: "Why did you use standalone Tomcat instead of Spring Boot?"
> **Answer:**  
> *"Embedded Spring Boot JARs abstract away the container runtime. Standalone Tomcat 10 on Jakarta Servlet 6 allows us to use the Tomcat Manager reload API (`/manager/text/reload`), container-managed connection pooling, and JMX telemetry. In an enterprise bank or platform modernization setting, applications run in dedicated application containers, and understanding context reloads without JVM restarts is critical for zero-downtime reliability."*

### Q7: "How did you prove that approvals cannot be double-spent?"
> **Answer:**  
> *"In PostgreSQL, we execute an atomic `UPDATE approvals SET used_at = now() WHERE id = $1 AND used_at IS NULL RETURNING id`. Because PostgreSQL serializes row updates using tuple exclusive locks, the first thread wins and any concurrent threads update 0 rows. In our test suite (`test_safety.py`), we use Python's `threading.Barrier` to synchronize 8 independent database connections racing to execute the same approval. Exactly 1 wins, and 7 fail with `already used`."*

### Q8: "What is an mSPRT test?"
> **Answer:**  
> *"mSPRT stands for mixture Sequential Probability Ratio Test. In standard A/B testing, continuously peeking at $p$-values as incidents arrive and stopping early inflates false positive rates from 5% up to 30%+. mSPRT provides a continuous monitoring boundary that guarantees valid Type I error control regardless of sample size or peeking frequency, making it safe for live production monitoring."*

### Q9: "Why can't configuration reverts be covered by a Standing Contract?"
> **Answer:**  
> *"A Standing Contract represents automated authorization for repeat incidents without human intervention. Restarts or pool resizing have stable, static parameters (`app=ledgerline`, `size=20`). A Git configuration revert requires a specific `commit_sha` parameter that is unique to every incident. A contract for reverts would mean giving the agent permission to revert whatever commit it thinks is guilty—a dangerous blank check. Therefore, in our safety model, reverts are marked `contractable=False` and must always be signed by a human."*

### Q10: "What was your role vs. IBM Bob's role in this project?"
> **Answer:**  
> *"I designed the architecture, selected the enterprise technology stack (Java 21 servlets, Tomcat 10, PostgreSQL), and defined the mathematical and safety invariants. I leveraged IBM Bob as an AI pair-programmer for rapid implementation—specifically to derive the McNemar contingency formulas, harden defensive bash parameter handling, and generate multi-threaded race condition tests. Bob served as a developer productivity multiplier, cutting my boilerplate scaffolding time by over 40%."*
