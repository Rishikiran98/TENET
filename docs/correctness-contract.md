# Correctness contract, amendment A1

This amendment implements the October 2026 correctness action plan and takes
precedence over earlier guarantees in CLAUDE.md. The principal, policy code,
Python process and host remain trusted. Brain outputs and retrieved text do not
grant authority. This is a bounded prototype, not a hostile-host sandbox.

1. Events, proposals, constraints and embeddings are immutable snapshots.
   Log append order is authoritative; ULID lexical order is not.
2. A task ID and grant ID can start only once in one authoritative log.
   Repeated starts are rejected, including after failure. Automatic task resume
   is deliberately unsupported in this slice. Reopening the same durable log
   preserves rejection; using a new independent log creates a new trust domain.
3. Reads and accepted dispatches require unexpired authority. Equality with the
   expiry timestamp is expired. Clocks are explicitly injectable. Dispatch is
   rechecked after human approval; accepted effects need not finish before expiry.
4. Every dispatched attempt consumes budget, including failed or indeterminate
   attempts. Policy blocks do not. This slice has no automatic tool retry.
5. Approved decisions bind to immutable arguments, task, grant, policy version
   and expiry. A changed binding fails closed. Justification and arguments are
   displayed as quoted untrusted content, not approval instructions.
6. The executor independently checks tool/grant equality and supported file
   constraints. All symlink path components are rejected. Both logical and
   resolved paths stay in the sandbox and allowed subtree. Concurrent hostile
   filesystem mutation, hard-link provenance and OS isolation are out of scope.
7. A dispatch event precedes an effect. An outcome follows it. A missing outcome
   is indeterminate, never evidence of no effect. Arbitrary external effects and
   event appends are not atomic. No exactly-once claim is made.
8. Expected pre-effect errors become action.failed. Errors with uncertain effects
   become action.indeterminate. Log failure is surfaced, not hidden. Task
   completion means controller termination, not independently scored success.
9. Raw ingest accepts an optional namespace-scoped idempotency key. Reusing it
   with changed content, source or media type is rejected. Projection failure
   exposes the accepted raw ID; retry resumes projection without a second append.
10. Rebuild verifies first, derives into shadow stores and swaps only on success.
    The source log is unchanged by replay; tools never run during replay.
11. Retrieval order is stable under ties and includes transform identity in its
    evidence receipt. Custom embedders must identify their configuration. Historical
    evidence is recorded separately from re-derivation with a new transform.
12. Supplied citations must refer to the retrieved context. Empty citations are
    valid for actions based directly on the user's task. A valid reference is
    not proof of model causation.

Fixture policy for tests/comparison: permitted reads/writes are allowed; deletes
escalate to the scripted approver; unmatched tools are denied. This is an
evaluation fixture only. Production policy remains separately authored by Sai.

Validation: tests/test_correctness.py maps to audit F01-F14; durable and comparison
tests cover the additional storage boundary. Exclusions and incomplete work must
be carried into results rather than counted as passing tests.
