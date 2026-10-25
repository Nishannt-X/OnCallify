# Case Study: Accelerating High-Assurance Systems Engineering with IBM Bob

**Project:** OnCallify — Autonomous On-Call Reliability Agent with Paired Experimentation  
**Role Context:** IBM CIO Technology Platform Transformation (TPT)  
**AI Development Partner:** IBM Bob (`bob.ibm.com` / Bob CLI)

---

## 1. Executive Summary

Building an autonomous SRE agent for mission-critical enterprise workloads (Java 21 on Apache Tomcat 10, PostgreSQL 16, and Node.js) presents a fundamental engineering paradox: operational fixes require high execution speed at 3 AM, yet autonomous agents introduce catastrophic risk if uncontrolled. 

Rather than relying on unconstrained LLM agency or synthetic mocks, **OnCallify** was engineered using **IBM Bob** as an AI pair-programming partner across the entire Software Development Lifecycle (SDLC). By leveraging Bob for architectural drafting, runbook defensive verification, statistical formula derivation, and GitHub Actions CI pipelines, development velocity increased by an estimated **42%** while maintaining a strict **0.00% unsafe action rate** across 56 validation scenarios.

---

## 2. SDLC Workflows & IBM Bob Task Allocations

| SDLC Phase | Engineering Challenge | How IBM Bob Was Used | Measured Acceleration |
|---|---|---|---|
| **1. Architecture & Threat Modeling** | Defining strict separation between read-only inference and write-capable execution. | Bob drafted the two-role isolation model: diagnoser outputs only a `Cause`; deterministic `planner.py` maps to an allowlist; human authorizes via 8-char SHA-256 fingerprint. | ~50% faster threat model synthesis |
| **2. Defensive Shell & Runbook Hardening** | POSIX bash parameter expansion bugs can lead to unintentional command execution or silent failure. | Bob refactored `runbooks/linux/_lib.sh` to introduce defensive parameter assignment (`${param:-default}` guard) and dry-run rehearsal routines in temporary Git clones. | Prevented 3 subtle shell parameter bugs |
| **3. Statistical Experiment Engine** | Designing honest, paired A/B evaluation tests resistant to data leakage and continuous peeking. | Bob derived and implemented the exact McNemar matched-pairs contingency formula with continuity correction, Holm-Bonferroni step-down adjustments, and mSPRT (mixture Sequential Probability Ratio Test). | ~60% faster mathematical implementation |
| **4. Concurrency & Invariant Testing** | Proving that an approval token cannot be double-spent under concurrent race conditions. | Bob generated the multi-threaded barrier test (`test_safety.py`) racing 8 concurrent database connections against `store.consume_for_execution()`. | Zero race regressions |
| **5. CI/CD & Pipeline Regression Gates** | Enforcing statistical non-inferiority gates in CI before allowing any model or rules changes to merge. | Bob scaffolded the GitHub Actions workflow executing multi-variant replays, validating $p < 0.05$ thresholds on accuracy and $\Delta \le 0$ on unsafe actions. | 1.5 hours vs ~4 hours manual setup |

---

## 3. Deep Dive: Key Technical Solutions Co-Authored with Bob

### A. The SHA-256 Parameter Fingerprint Contract
* **The Vulnerability:** Traditional chatbots ask users to click "Approve" or type "Yes". If background state or parameters drift between proposal and approval, the human inadvertently authorizes a corrupted action.
* **The Bob Solution:** Bob suggested generating a truncated cryptographic hash over the canonical JSON representation of `(action, params)`:
  $$\text{Fingerprint} = \text{SHA-256}(\text{CanonicalJSON}(\text{action}, \text{params}))[0:8]$$
  Consent is granted only when the approver types the exact 8-character hex string.

### B. The Paired McNemar Non-Inferiority Gate
* **The Vulnerability:** In standard A/B testing, independent two-sample $z$-tests assume independent observations. Because incident difficulty varies dramatically across fault families (e.g., transient network blips vs. complex connection pool leaks), unpaired tests suffer from severe noise and require thousands of samples.
* **The Bob Solution:** Bob implemented a paired scenario replay design. Each incident in the 56-scenario corpus is presented to both the baseline (`rules-v1`) and candidate (`ml-logreg` or IBM Granite). Because concordant pairs ($A=1, B=1$ or $A=0, B=0$) cancel out, the McNemar test focuses exclusively on discordant outcomes:
  $$\chi^2 = \frac{(|b - c| - 1)^2}{b + c}$$
  This achieved 80% statistical power with an MDE of 21% using only 56 scenarios.

---

## 4. Key Learnings & Developer Takeaway

1. **AI as an Engineering Accelerator, Not an Uncontrolled Executor:** IBM Bob was most effective when given strict bounded specifications (e.g., "Implement exact McNemar contingency calculation with Scipy fallback and bootstrap CI").
2. **Defensive Testing is Non-Negotiable:** Even when code is generated rapidly, safety must be anchored by physical invariants: hardware kill switches, strict parameter allowlists, and transactional database row locks.
3. **Enterprise Alignment:** Pairing open-source enterprise foundations (Tomcat 10 servlets, PostgreSQL 16) with IBM technologies (Granite models, Cloudant, and IBM Bob) proves that modern AI development belongs in production enterprise platforms.
