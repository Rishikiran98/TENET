# Amendment A3: committed dispatch and journal reconciliation

October 10, 2026 (America/New_York). Package version 0.4.0.
This extends A2. Production policy remains Sai's responsibility.

## Decision record and task

The bounded task is to read a small work note, reject a deterministic fixture's
attempt to write outside the grant, and append the normalized report to a named
journal. The independent success oracle reads the note, protected file, and
journal database directly. Controller termination is not the oracle.

Use a transactional SQLite journal rather than inferring operation provenance
from a filesystem value. This deliberately restricts recovery to a tool that
controls both its effect and an operation ledger in one transaction. Ordinary
filesystem operations retain A2 uncertainty. No automatic task resumption or
effect retry is added.

## Boundaries and states

* A dispatch must commit outside any caller log transaction before an effect.
  `EventLog.append_committed` rejects nested use. Custom logs must implement this
  boundary. In-memory logs still provide process-local evidence only.
* `journal.append` takes exactly `journal` and `content`. Both gate and executor
  require an exact `journal` constraint, an ASCII journal name, and UTF-8 content
  of at most 4096 bytes. A new journal executor receives the durable proposal
  occurrence ID from the loop, never from Brain arguments.
* Dispatch records the journal store's persistent random identity. The operation
  binding covers proposal digest, task, grant, namespace, and store identity.
  A different store, reused ID with changed binding, or malformed trace fails
  closed. The store is trusted and may not be rolled back independently of its
  effects. Missing/replaced stores are not evidence that an effect never ran.
* Store operation states are absent, applied, or cancelled. An append atomically
  inserts the report and applied operation. Reconciliation atomically returns an
  applied receipt or changes absent to cancelled. Cancellation fences even a
  still-running original worker. SQLite write transactions arbitrate ownership;
  there are no expiring leases or competing effect retries.
* Reconciliation commits store evidence before appending `action.reconciled` to
  the event log. A crash between those commits is recoverable by repeating the
  inspection. The log serializes competing reconcilers and records one terminal
  reconciliation event. No locks are held by the journal while acquiring a log
  lock, preventing reversed lock order with the original worker.
* Applied proves that this bound operation committed its report. Cancelled
  proves that this store did not and cannot apply that operation. Neither says
  a human's broader task succeeded. Unavailable/corrupt/mismatched evidence
  remains unresolved. An audit validates recorded relationships, not external
  truth; the management API checks the authoritative store.
* Reconciliation is a trusted operator API, not a Brain tool. It can run after
  grant expiry because it performs no new task effect. It never reuses approval,
  broadens a grant, refunds an attempt, or creates a new dispatch. The original
  dispatch counts even when reconciliation cancels it. Task/grant reuse stays
  prohibited. A cancelled or never-dispatched task requires a human decision
  and fresh authority for further work.

## Migration and review fixes

Schema 3 adds one event type and journal dispatch identity. Schema 1 and 2 stay
readable without rewriting; existing schema-2 authority remains auditable.
Schema-1 traces cannot certify modern authority. Existing compatibility imports
and ordinary executor `execute(proposal, tool_grant)` remain supported. A journal
executor additionally implements `execute_operation` with the dispatch receipt.

Malformed or duplicate outcomes cannot erase uncertainty. Task authority is
bound to its original namespace. These are repairs, not architectural novelty.

## Limits and validation plan

Exercise real process exits before dispatch commit, after dispatch commit,
after the effect, during reconciliation, and after reconciliation evidence.
Run competing reconcilers and original-worker/cancellation races. Compare an
independent ordinary-table controller and journal implementation using the same
task, crash schedule, and external oracle. Report completed tasks, violations,
duplicates, unresolved operations, interventions, audit errors, and repeated
timings separately. Process death is not power loss. Trusted-host, external
anchor, hard-link, legacy-read-content, live projection refresh, and native
Windows limitations from A2 remain unchanged.
