# Review status and validation

**October 11, 2026 status (0.4.0).** A3 added the transactional journal and
operator reconciliation, so the reconciliation limit below now applies only to
ordinary filesystem effects. Later review rounds fixed the issues recorded in
`tests/test_bugfixes.py`. Run `pytest` for the current count; the tests in
`tests/test_pgvector.py` skip unless `TENET_PG_DSN` is set. The table and
measurements below are the 0.3.0 record and are kept unchanged.

Updated October 10, 2026 for version 0.3.0. The six previously open items and the
three argument-validation observations are addressed in this patch. The earlier
75-test and 110-test results were incomplete evidence; the current suite passes
161 tests. This is evidence for the tested contracts, not a claim of exhaustive
correctness or production readiness.

| Review item | Implemented behavior | Evidence |
|---|---|---|
| Audit matching | Follows proposal-specific causal references through initial verdict, approval request/decision, dispatch verdict, and outcome. Checks task and namespace identity. Recomputes request bindings. | Borrowed verdicts/approvals, fabricated matching bindings, cross-task references, and valid interleaved traces in `test_causal_audit.py`. |
| Required receipt fields | New appends use schema 2. Dispatch time, approval bindings, and outcome operation/digest/grant bindings are mandatory and type-checked. Verdicts identify proposal and phase. | Missing-field and invalid-time regressions; legacy SQLite rows remain readable, while schema-1 appends are rejected. |
| Brain feedback | `propose(task, context, *, observations=...)` receives an immutable history. Step results expose the same observations. | An adaptive Brain responds to a block, a missing-file failure, and a successful read, then completes. |
| Read receipts | Reads return at most 4,096 bytes of valid UTF-8 text. The Brain receives that bounded text; new read outcome events omit it and retain metadata plus the returned-prefix SHA-256. | Large/multibyte files, UTF-8 boundaries, invalid encoding, and an oversized custom-executor result. |
| SQLite scaling | Normal transactions load only rows after the connection's last sequence. Rollback restores surviving state. `verify()` explicitly rereads all rows. | Decode-count regression, separate writers, nested rollback, disk-tampering check, and measured append scaling below. |
| Reopen hydration | MemoryCore automatically verifies and rebuilds projections from a populated log before construction returns. Failure propagates. | Retrieval after reopen without manual rebuild; explicit contextualizer failure during construction. |
| Unconstrained arguments | Built-in filesystem schemas are checked even with an empty constraints mapping. Missing, malformed, and unsupported arguments are blocked before dispatch. | An invalid proposal followed by a valid read still succeeds with budget one. |
| Additional path operands | Current tools reject extra fields such as `dest`. Unknown `fs.*` tools fail closed until an explicit schema is implemented. | Extra-destination and future-copy regressions. No copy/move tool was added. |
| Backslashes | Logical paths and prefixes reject backslashes, colons, and trailing-dot/space components on every platform; resolved paths must also stay within the granted prefix. | Gate and direct-executor lexical tests run on Linux. Native Windows integration was not run. |

The earlier circular-import and path-prefix fixes remain covered by fresh
interpreter imports and gate/executor regressions.

## SQLite measurements

Same fixed payload, Python 3.12.14, SQLite 3.53.1, WAL journal, FULL synchronous
mode, temporary local databases. Each cell is one timing run; verification is
performed after the timed appends. These measurements do not establish physical
power-loss durability of the execution environment or production throughput.

| Appends | Before incremental loading | After incremental loading |
|---:|---:|---:|
| 500 | 0.920 s | 0.026 s |
| 1,000 | 3.580 s | 0.047 s |
| 2,000 | 14.454 s | 0.078 s |

Raw results: `evaluation/results/sqlite-before.json` and `sqlite-after.json`.
The before source is local commit `ac05117`, identical in content to GitHub
commit `ded3e5e`; the after source is this patch. `evaluation/sqlite_scaling.py`
reproduces the workload. A separate regression verifies appends do not repeatedly
decode historical records, without relying on timing thresholds.

The shared nine-case comparison still passes 9/9, and the real process-crash
experiment still passes 4/4. These remain deliberately limited experiments.

## Compatibility and remaining limits

See [amendment A2](correctness-contract-a2.md) for API and schema migration.
Existing schema-1 records are not rewritten, redacted, or falsely certified as
schema 2. They may contain historical full read content. A digest receipt cannot
reconstruct omitted text, and a later Brain proposal may itself quote observed
text into logged arguments or justification. These changes bound the direct read
result path; they are not a general information-flow or retention policy.

Custom Brains must accept the observations keyword. Tool output is untrusted.
Indeterminate effects still stop execution and tasks are never resumed. Since
A3, only `journal.append` dispatches can be reconciled (`reconcile_journal`);
ordinary filesystem effects remain unresolved. Native Windows, a real LLM, and production-scale concurrent
workloads were not evaluated. Hostile-host isolation and hard-link provenance
remain outside the contract. Amendment A4 adds external log anchors, which
detect suffix truncation and wholesale replacement; storing them off-host is
the operator's responsibility.

Incremental loading assumes normal append-only writers. Explicit verification
rereads historical database rows, but cannot detect valid suffix removal or whole
log replacement without an independent anchor. Task registration and explicit
verification still scan history. Automatic startup hydration is a full rebuild;
multiple already-running MemoryCore instances still require explicit refresh to
see each other's new memory projections.
