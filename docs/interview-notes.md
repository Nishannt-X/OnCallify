# Interview notes: OnCallify

Short answers first, then the detail. Every answer points at the code that backs it.

## The 30-second pitch

"OnCallify is an on-call assistant for a Java service on Tomcat. When an alert fires it
collects the logs, metrics and recent changes, diagnoses the cause, and proposes exactly
one fix from an allowlist. A human approves it by typing a fingerprint of the exact
action and parameters; the fix runs through a bash or PowerShell runbook; and the incident
only closes when three independent checks prove recovery. The part I am proudest of is
that the assistant is itself measured: I capture real incidents by breaking the running
service, replay them through different diagnosers (rules, a logistic regression, IBM
Granite), and compare them with paired statistics. CI blocks a diagnoser change unless it
is non-inferior on correct fixes and on unsafe actions."

## Architecture and stack

**Why so many languages?** Each piece uses what fits it, the way a real platform does: the
watched service is Java on Tomcat because that is what many enterprise services are; the
gateway is a thin, I/O-bound edge, which Node does well; the agent and statistics are in
Python for numpy/scipy/scikit-learn; runbooks are shell and PowerShell because that is what
operations teams run on Linux and Windows hosts.

**Why a WAR on standalone Tomcat instead of Spring Boot's embedded server?** I wanted the
operational surface of a real container: the manager API (`/manager/text/reload` is how
`restart_app` works), context reloads, the JDBC pool as a container concern, JMX
thread-pool metrics. Embedded is simpler to ship; external is what many enterprises still
operate, and what the on-call engineer actually touches.

**Why PostgreSQL and MongoDB?** Different access patterns. Approvals and contracts need
transactional, constrained, conditional updates (exactly-once consumption, `uses <=
max_uses`). Raw events are append-heavy and schemaless; Mongo's unique index on `event_id`
gives idempotent ingest under concurrent writers. At small scale I would drop Mongo and use
a Postgres table with a unique index and JSONB; that is a fair trade-off to say out loud.

**Where is Cloudant?** `store/transcripts.py`: diagnosis transcripts can go to MongoDB or
IBM Cloudant through the same interface; Cloudant uses its CouchDB HTTP API with an IAM
token from an API key, cached until expiry. It is unit-tested against mocked HTTP.

## Safety (expect most questions here)

**What stops the model from doing something dangerous?** It cannot choose an action at
all. Diagnosers output a cause; the planner (`planner.py`) maps causes to one allowlisted
action; `actions.validate` rejects unknown actions, missing or extra params, wrong types and
out-of-range values. Even a perfectly adversarial model can at worst pick the wrong cause,
which yields a wrong-but-allowlisted proposal that a human sees, with its blast radius and a
dry-run result.

**Why type a fingerprint instead of clicking Approve?** The fingerprint is the first 8 hex
characters of SHA-256 over `(action, params)`. Typing it proves the human approved *this*
action with *these* parameters. If anything about the proposal changed, the fingerprint
changes. "Yes, do it" approves nothing specific.

**How is "approved once" enforced?** `UPDATE approvals SET used_at=now WHERE id=$1 AND
used_at IS NULL AND expires_at > now RETURNING ...` (`store/postgres.py`). One statement, so
the database serialises racing executors; plus a partial unique index allows at most one
consumed approval per proposal. `tests/test_safety.py` races eight separate database
connections and asserts exactly one wins.

**What if someone edits the proposal row after approval?** At execution,
`consume_for_execution` recomputes the params hash from the stored proposal and compares it
to the hash the human approved; a mismatch refuses execution (tested).

**Why can't a revert be covered by a contract?** A contract is "next time, do exactly this,
without asking". A revert's parameter is a commit id that differs every incident, so a
contract would have to say "revert whatever looks guilty": a blank cheque. Restart, enabling
a flag, syncing a credential and resizing to a fixed size have stable params, so they can be
contracted (`ActionSpec.contractable`).

**How do you know a fix worked?** Three checks (`verify.py`), all required: every paging
alert resolved *after* the fix (an earlier resolve does not count), infrastructure errors did
not increase across two scrapes, and the action's post-condition holds (for a restart, the
app's `started_at` is after the execution started; for a revert, the touched keys are back to
their earlier values in the file). The model's opinion is not one of the checks.

**What is the dry run, and why does the runbook do it?** Each runbook has `--dry-run`, which
checks every precondition with the same privileges as the real run. A revert is rehearsed in
a throwaway clone, because my first version used `git apply --check`, which is stricter than
`git revert`'s merge and refused a revert that would have worked (`docs/LEARNINGS.md`).

**What does the kill switch do?** A file whose existence blocks every real execution, even of
already-approved actions; dry runs still work so diagnosis continues. Engaging it is audited.

## The experiments (the data science part)

**How do you build a labelled dataset for incident diagnosis?** I cause the incidents.
`eval/scenarios.py` breaks the running stack 7 ways × 8 variations (load level, which
config key, provider delay, blip length) and, in some variations, plants a red herring (an
unrelated config commit or secret rotation just before the fault). It waits for the real
alert from the real rule engine, then builds evidence with the same code the live agent
uses. The label is known because I injected the fault. Nothing in the evidence is synthetic.

**Why a paired design?** Every variant sees exactly the same incidents. Incident difficulty
is the biggest source of variance; pairing removes it, so a 56-incident corpus can detect
differences that two independent samples of 56 could not.

**Why McNemar and not a two-proportion z-test?** The outcomes are paired binaries (did A get
this incident right, did B). Only discordant pairs carry information about the difference;
McNemar tests whether those split 50/50. A two-proportion test assumes independent samples,
which these are not, and would be badly mis-calibrated. With small counts I use the exact
binomial version.

**What changes for the LLM?** It is nondeterministic, so I run it k times per incident. The k
runs of one incident are correlated, so the unit of analysis is still the incident: cluster
bootstrap (resample incidents, not runs) for the interval, and a cluster sign-flip
permutation test for the p-value. Treating k × n runs as independent would shrink the
intervals and inflate false positives; `test_cluster_bootstrap_resamples_scenarios_not_runs`
shows the difference.

**Why Holm?** Four metrics (cause correct, correct fix, unsafe, missed) means four tests;
Holm controls the family-wise error rate and is uniformly more powerful than Bonferroni.

**How do you know your statistics code is right?** A/A simulation (`stats.validate_tests`):
2,000 simulated experiments where the null is true by construction. McNemar and the
sign-flip test reject ~2.5% (conservative, as exact tests on discrete data are), bootstrap
95% intervals cover the true zero ~95%. And it demonstrates the peeking problem: checking a
z-test 50 times and stopping at the first p < 0.05 gives ~31% false positives; always-valid
mSPRT p-values at the same looks give ~3%.

**What can 56 incidents not tell you?** The report prints the minimum detectable effect
(McNemar power at 80%, given the observed share of discordant pairs). With ~56 pairs it is in
the high teens of percentage points, so I do not claim small improvements.

**What is the online experiment?** The gateway assigns each live incident to a variant by
hashing `salt:experiment:incident_id` into 10,000 buckets: deterministic (sticky) without
storing state, and independent across experiments because each has its own salt. Changing the
salt of a running experiment is refused (409) because it would reshuffle units between arms.
Exposure is logged separately, when the variant actually made the decision, and an SRM
chi-square check compares arm counts to the configured split: a tiny p-value means a bug in
assignment or logging, and the results must not be trusted.

**Assignment vs exposure, why both?** Assignment is "which arm would this unit get";
exposure is "this unit actually experienced the arm". If you analyse assignments that were
never exposed you dilute the effect; if exposure logging depends on the arm (one arm crashes
before logging), you get SRM. Logging both lets you detect that.

**Why logistic regression and not a bigger model?** Around 56 labelled incidents and 34
features. A regularised linear model with standardised inputs is the honest choice, gives
usable probabilities, and its coefficients explain predictions ("which signals pushed it to
this cause"). It is scored with leave-one-group-out cross-validation so it is never tested
on a variation it trained on. Weights are stored as JSON, not pickle, so loading a model can
never execute code.

**How do you evaluate an LLM fairly?** Same evidence, same planner, same scoring. Its output
is validated as untrusted input: the cause must be in the enum, the suspect commit must exist
in the evidence (models invent commit ids), confidence is clamped. Answers are cached per
model, prompt hash and seed so reports are reproducible. Retrieval (RAG over past incidents)
excludes the incident being scored, otherwise the model could retrieve its own answer.

**What is the non-inferiority gate?** For a diagnoser change, the lower confidence bound of
(candidate − baseline) correct-fix rate must be above −5 points, and the unsafe-action rate
must not get worse by more than 2 points. A candidate does not need to be better to ship, but
it must be provably not worse.

## Failure stories (pick two)

See `docs/LEARNINGS.md`. The best ones:
1. the latency confounder: a leak diagnosed as a slow provider, caught by the human gate;
2. the dead service that looked healthy because it stopped reporting, fixed with
   `scrape_failed` (the Prometheus `up` idea);
3. the contract that did not match because alert order is a race;
4. the dry run that disagreed with the real run (`git apply --check` vs `git revert`).

## If I had more time

* run the fixes against a fleet, with canaries (fix one instance, verify, then the rest);
* calibrate confidence (reliability diagrams) and set the contract confidence floor from data;
* replace the hashed bearer token with SSO and per-action approval policies (two approvers
  for disruptive actions);
* a larger corpus, so the experiments can detect smaller differences.
