# Correctness contract, amendment A2

October 10, 2026. Version 0.3.0. This amendment extends A1 and takes precedence
where their interface descriptions differ. Production policy remains separately
authored by Sai. The principal, policy implementation, process, and host are
trusted. This patch does not add automatic retries or task resumption.

1. **Argument validation precedes policy.** `fs.read` and `fs.delete` accept only
   `path`. `fs.write` accepts exactly `path` and UTF-8 string `content`. All
   three require valid logical paths even without grant constraints. Additional
   fields are rejected. New filesystem tools require an explicit validator.
   Backslashes, colons, and components ending in dots or spaces are prohibited
   in paths and prefixes. Logical paths
   are interpreted relative to the sandbox; a leading slash names its root.
   The executor repeats argument checks and retains real-path/symlink checks.
2. **Audit authority is proposal-specific.** The auditor follows causal links,
   checks task and namespace identity, reconstructs approval bindings, and
   requires a matching dispatch-phase verdict. A task's most recent verdict or
   approval is never sufficient to authorize another proposal. A denial after a
   policy change is valid; the prior approval cannot authorize a new-policy
   dispatch.
3. **Schema 2 is the write contract.** New events default to version 2 and cannot
   opt into version 1. Version 1 remains readable for historical hash validation
   and memory hydration. The audit flags legacy traces as insufficient for
   version-2 certification. No automatic event rewriting occurs.
4. **Required authority receipts.** Proposal events require a digest. Gate
   verdicts require `proposal_ref` and `phase` (`initial` or `dispatch`). Approval
   requests require a SHA-256 `binding`; decisions require `proposal_ref` and a
   binding field. Approved bindings must be SHA-256 values. Rejections may retain
   an empty or invalid supplied string as evidence of why approval failed.
   Dispatches require a timezone-aware ISO `accepted_at`. Executed, failed, and
   indeterminate outcomes require `operation_id`, `proposal_digest`, and
   `grant_id`. Structural references are independently checked by the auditor.
5. **Brain feedback is explicit.** The Brain receives
   `propose(task, context, *, observations=...)`. Observations are immutable
   snapshots containing proposal, outcome event ID/type, result or error, and
   rationale. Previous history tuples do not grow or mutate. Successful, blocked,
   and failed steps feed the next proposal. An indeterminate step is exposed in
   TaskResult and stops the loop. Context and observations remain untrusted data.
6. **Read data and receipts are separate.** The built-in file reader reads at
   most 4,097 bytes to return a UTF-8 prefix of at most 4,096 bytes and identify
   truncation. Invalid UTF-8 inside that prefix is a known read failure. The
   Brain receives the prefix. New read outcome events retain path, byte count,
   truncation flag, and SHA-256 of the returned prefix, but omit content. The
   loop also bounds text returned by custom `fs.read` executors. Arbitrary
   non-text custom read receipts retain a result digest. No immutable content
   archive is created, so receipt replay cannot reconstruct omitted read text.
7. **Incremental appends, full verification.** SQLite transactions synchronize
   only newly appended sequence rows. Causal-ID existence uses an in-memory
   index. Rollback reloads surviving database state. Explicit `verify()` rereads
   all persisted rows and checks the complete chain and each row's schema. This
   does not add an external anchor or make arbitrary database mutation safe.
8. **Ready after reopen.** Constructing MemoryCore with a populated log performs
   verified projection hydration before returning. Failure is explicit. Other
   live MemoryCore instances are not automatically refreshed by this startup
   guarantee; their caller still invokes `rebuild_projections()` when required.

Validation and measurements are recorded in [review follow-up](review-follow-up.md).
The event taxonomy remains 15 types. The API/package version changes to 0.3.0
because the Brain protocol and write schema are stricter. Existing `Proposal`
and `ToolExecutionError` compatibility imports remain supported.
