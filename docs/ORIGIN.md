# Origin and credit

OnCallify's safety model is adapted from **Beacon**
(https://github.com/Prashant-thakur77/Beacon, MIT License, (c) 2026 Prashant Thakur),
an AWS voice on-call agent built for two hackathons. Beacon's MIT license text is
reproduced in `LICENSE-BEACON`.

## Ideas taken from Beacon (re-implemented, not copied)

| Beacon idea | Where it lives here |
|---|---|
| approval as a stored record bound to a params hash, single use, expiring | `agent/src/oncallify/approvals.py`, `store/` |
| standing "Sleep Contracts" (alarm + action + exact params, uses, TTL) | `approvals.grant_contract`, `matching_contract` |
| allowlist registry with dry run, execute, post-condition, per-action verify budget | `agent/src/oncallify/actions.py`, `runbooks/` |
| "recovered" requires the alarm to clear after the fix + metric quiet + post-condition | `agent/src/oncallify/verify.py` |
| the dry run is executed by the component that will execute | runbooks dry-run themselves |
| a kill switch and an audit trail | `runbooks.py`, `store` |

Beacon's code is AWS-specific (Lambda, Step Functions, DynamoDB, Bedrock, CloudTrail) and
voice-specific (AssemblyAI, Twilio, Telegram). None of that code is in this repository.

## New in OnCallify

* Ledgerline, the Java/Tomcat service with real, externally caused failures
* the Node/Express gateway: idempotent ingest, alert rule engine, experiment assignment
* the cause/plan separation (diagnosers cannot choose actions)
* the bash + PowerShell runbooks with a shared contract and tests
* the change ledger over a config git repository and a vault audit feed
* PostgreSQL, MongoDB and IBM Cloudant stores; FastAPI service; React dashboard
* the rule, ML (logistic regression) and IBM Granite diagnosers
* the replay corpus captured from the live stack, and the whole experiment layer:
  paired replay, McNemar, cluster bootstrap and sign-flip, Holm, power/MDE, SRM,
  non-inferiority gate, mSPRT, A/A validation
* everything in `docs/LEARNINGS.md`
