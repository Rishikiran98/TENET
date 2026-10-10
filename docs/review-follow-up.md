# Review corrections and remaining work

October 10, 2026. This patch prioritizes the two requested blockers: independent
public imports and enforcement of granted logical paths before dispatch.
The earlier claim that the repair closed the correctness gaps was too broad.
The 75-test suite and nine-case comparison missed these contract violations.

## Fixed in this patch

1. **Circular imports:** `Proposal` and `ToolExecutionError` now live in neutral
   `tenet.contracts`. The gate and executor no longer depend on agent package
   initialization. Existing agent imports remain aliases. Five separate fresh
   interpreters exercise the public entry points.
2. **Gate path authority:** logical `path_prefix` checks precede policy, approval,
   and dispatch. Unsupported and malformed constraints fail closed. The gate
   does no filesystem I/O; the executor independently repeats logical validation
   and checks symlinks and real-path containment. An out-of-prefix request emits
   `action.blocked` and does not consume budget. One integration regression blocks
   a protected delete, then successfully deletes an allowed note with budget one.
3. **Audit path checks:** a trace containing an out-of-prefix dispatch is flagged,
   even when a custom gate allowed it and the executor refused it. This fixes
   the path-specific audit omission; proposal-causal authorization matching is
   still pending below.
4. **Documentation and metadata:** implementation status, module layout, event
   count, gate responsibility, and the hash-chain truncation caveat are corrected.
   Package metadata now matches the existing runtime version, 0.2.0.
   `requirements.txt` delegates to `pyproject.toml` to avoid dependency drift.

The new regression file initially produced 29 failures and 6 passes. After the
fix, the complete suite passes 110 tests. The old comparison is a bounded
historical result; it should not be treated as exhaustive correctness evidence.

## Remaining work, in suggested order

| Issue | Current behavior | Concrete acceptance criterion |
|---|---|---|
| Proposal-causal audit matching | Verdicts and approval status are keyed by task | An approval or allow verdict for proposal A cannot justify proposal B, including interleaved or malformed traces; follow causal references and check task identity. |
| Required receipt fields | Several A1 fields are optional at append time | Missing or invalid dispatch time, approval binding, and outcome operation binding are rejected under an explicit schema version; preserve a defined A0 read path. |
| Brain outcome feedback | Every proposal sees task text and the same retrieval | A deterministic adaptive Brain uses prior success, block, and failure observations to choose a different next step; observations are immutable and remain untrusted. |
| Unbounded read receipts | Full fs.read text is copied into permanent events | Define a byte cap and truncation marker, or an access-controlled content reference with a digest; test large and multibyte input and define retention. |
| SQLite full-table reloads | Every transaction parses all retained events | Load incrementally by sequence, preserve rollback and cross-connection visibility, and demonstrate improved append scaling at 500, 1,000, and 2,000 events. |
| Reopen hydration | Existing logs require explicit rebuild before retrieval | Reopening a populated log yields usable projections by default, or construction fails clearly; hydration failure must not silently present an empty memory. |

The SQLite timings in the review were supplied by the reviewer; this focused
patch did not rerun that scaling experiment. Source inspection confirms the
full-table reload. It also confirms missing Brain feedback, unbounded read
receipts, explicit-only hydration, loose receipt requirements, and task-keyed
audit authorization. None of these are claimed fixed here.

Suffix truncation remains outside an unanchored hash chain's guarantees. The
README and design document now state this beside the tamper-evidence claim.
Implementing external anchoring would require an explicit independent trust
boundary, not another value stored in the same modifiable database.
