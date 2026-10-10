# Correctness repair and conventional comparison

Measured October 10, 2026 against original commit
`c7a798368869dbee60923b45de2c09f3ab4d45c8`.

**Review update:** subsequent review found missed import and gate-constraint
bugs plus additional limitations. The two priority bugs are now repaired; see
[review follow-up](review-follow-up.md) for current status and the 161-test suite.
The measurements below describe the initial repair and its selected cases.

The initial repair addressed the initially reproduced gaps under A1. The
conventional implementation matches it on the shared action cases. These results
do not establish an advantage for TENET's event-sourced architecture.

## Evidence

The initial repaired test suite passed **75 tests**. The original suite had 42 tests.
The added coverage includes immutable nested values, task/grant reuse and races,
approval expiry and replay, filesystem constraints, projection failure and retry,
concurrent event appends, citation membership, durable reopen, dispatch/outcome
write failure, and uncertain effects. The ranking regression now requires a real
score margin and passes with either ingestion order.

The shared comparison uses explicit proposals, a scripted approver, temporary
files, and an injected clock. The policy is a test fixture: allow in-grant reads
and writes, escalate deletes, and deny unmatched tools. No production policy
was authored. A pass means the stated filesystem invariant held.

| Case | Original TENET | Repaired TENET | Conventional SQLite |
|---|---|---|---|
| Authorized write succeeds | Pass | Pass | Pass |
| Tool outside grant is blocked | Pass | Pass | Pass |
| Budget limits two proposals to one attempt | Pass | Pass | Pass |
| Stale approval binding is rejected | Fail | Pass | Pass |
| Approval crossing expiry cannot execute | Fail | Pass | Pass |
| Symlink cannot escape the granted prefix | Fail | Pass | Pass |
| Direct executor rejects wrong tool grant | Fail | Pass | Pass |
| Reusing a task cannot reset its budget | Fail | Pass | Pass |
| Unknown citation cannot authorize execution | Fail | Pass | Pass |
| **Total** | **3/9** | **9/9** | **9/9** |

The baseline is a small, independent implementation with ordinary SQLite task
and attempt tables. It shares no TENET enforcement code. It implements the
common action contract, not memory retrieval or every TENET feature. Accordingly,
this comparison establishes parity on these cases only. It does not compare
retrieval quality, implementation effort, throughput, or operational scale.

## A useful problem beyond TENET's bugs

A real process-death experiment produced the following result for **both** the
repaired TENET implementation and the conventional baseline:

| Process exits at | File effect exists after restart | Recorded outcome | Automatic task reuse |
|---|---|---|---|
| After dispatch commit, before effect | No | Indeterminate | Rejected |
| After effect, before outcome receipt | Yes | Indeterminate | Rejected |

All four experiment cases passed their safety assertions. A pending dispatch or
attempt cannot, by itself, distinguish these two external states. This uncertainty
survives correctness repairs and does not depend on TENET's implementation bugs.
An event log makes the incomplete operation inspectable; a conventional pending
attempt row does so too.

This is a real engineering problem, but this experiment does **not** establish a
new research problem or a TENET-specific solution. A useful next hypothesis is:
can tool-specific idempotency and reconciliation recover more legitimate work
without duplicate effects or authority violations, compared with the ordinary
SQLite baseline? That would require a separate bounded experiment with tools
that expose verifiable operation IDs or state. This repair deliberately stops
short of automatic recovery.

## What changed

- Events, proposals, constraints, and embeddings are immutable snapshots.
- Task IDs and grant IDs are claimed atomically and can start only once per
  authoritative log. `SQLiteEventLog` preserves this across reopen and separate
  connections; `InMemoryEventLog` is process-local.
- Expiry is enforced on reads and accepted dispatches. Approvals bind to the
  exact proposal occurrence, task, grant, policy version, and expiry. Agent text
  is quoted and labeled untrusted. A human can still be persuaded by that text.
- The executor checks the tool grant and rejects unsupported constraints and
  symlink components, including links to another subtree inside the sandbox.
- Dispatches are recorded before effects. Failed attempts consume budget.
  Uncertain effects abort continuation. Missing outcome receipts are exposed
  through `AuditWriteError` and the read-only structural trace audit.
- Ingestion supports a namespace-scoped `ingest_id`. Projection failures expose
  `ProjectionError.raw_id` and `.ingest_id`; a matching retry does not append a
  duplicate raw record. Changed content or metadata with the same key is rejected.
- Rebuild verifies the log, constructs shadow stores, and publishes only after
  success. Historical retrieval receipts retain text hashes, transform identity,
  and scores. Rebuild never executes tools.
- Embedders require explicit configuration identity. The optional semantic
  adapter requires a model commit revision and includes package versions in its
  identity. That adapter was not run or downloaded in this evaluation.

## Compatibility and limits

New event payloads include dispatch and indeterminate event types. Existing A0
traces can still be hash-verified, but they lack the receipts required for A1
structural auditing. They should not be presented as A1-compliant histories.
Custom event logs must implement serialized transactions; custom projection
stores need an `empty()` factory that returns an unpublished independent store.
Custom approvers must return the supplied binding to approve. Frozen mappings
can be serialized through `tenet.values.thaw` or `canonical`.

Tasks cannot resume, including after a failed start. Retry requires an explicit
operator decision and a fresh task/grant after inspecting possible effects.
Raw ingestion retries are a separate operation with an explicit idempotency key.
Policy versions must identify immutable policy semantics; policy code, principal,
process, and host remain trusted. A registry call checks tool grants and path
constraints but is not itself a complete task authority boundary.

The filesystem sandbox does not defend against concurrent hostile host mutation
or prove hard-link provenance. Hash chaining detects changed entries relative
to the retained chain; without an external anchor, it cannot prove that an entire
log was not replaced or a valid suffix removed. The structural audit checks
recorded relationships, not external truth or policy correctness. SQLite appends
and full-log scans favor simplicity here and have not been load-tested. Multiple
MemoryCore instances must explicitly rebuild to refresh each other's projections.

`task.completed` still means the Brain stopped proposing. It does not independently
score whether the user's goal succeeded. Valid citations establish membership in
the retrieved set, not causal influence. No real LLM, semantic retrieval benchmark,
human approval study, production backend, or broad security evaluation was run.

## Decision

The correctness work was worth doing: it turns several misleading guarantees
into explicit, testable boundaries. **These measurements do not justify expanding
TENET as a platform or claiming a research contribution.** Keep it as a compact
reference implementation. Further time is justified only by a measured advantage
over the conventional baseline, such as better reconciliation or audit diagnosis
on independently motivated failures.

Reproduction and raw evidence are in [evaluation/README.md](../evaluation/README.md)
and [evaluation/results](../evaluation/results/).
