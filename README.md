# TENET

A secure semantic memory layer for AI agents, with **governance as a
first-class property** — demonstrated by one thin action agent built on top of
it. v1 proves a principle, *governed memory*, not a hardened security product.

The full design contract — threat model, architecture, decision register, and
build order — lives in [CLAUDE.md](CLAUDE.md). Read it before changing anything.

## Correctness repair and comparison

The [A1 contract](docs/correctness-contract.md),
[A2 interface/schema amendment](docs/correctness-contract-a2.md) and
[A3 journal reconciliation amendment](docs/correctness-contract-a3.md) supersede
conflicting prototype guarantees in the original design. The repair includes immutable
records, single-use task/grant claims, bound approvals, dispatch expiry checks,
symlink rejection, ingestion retry keys, atomic projection rebuilds, and an
optional `SQLiteEventLog` for durability. Version 0.3.0 adds proposal-causal audits,
strict receipts and filesystem arguments, bounded Brain observations, incremental
SQLite loading, and automatic hydration on reopen. In-memory logs remain process-local.
The current version, 0.4.0 (A3), adds a transactional `journal.append` tool and
operator reconciliation of uncertain journal dispatches (event schema 3, 16 event
types); ordinary filesystem effects keep A2's uncertainty.

Current validation and API migration notes are in
[review follow-up](docs/review-follow-up.md).

See [measured results and limitations](docs/correctness-results.md) and
[evaluation reproduction instructions](evaluation/README.md). This is a bounded
correctness comparison, not evidence that TENET outperforms conventional code.

## The one idea

An agent's memory is a security surface. Tenet is **event-sourced end to end**:
a single append-only, hash-chained **event log is the only source of truth**,
and memory, the audit trail, and the agent's step history are all *projections*
folded from that log. Nothing is authoritative except the log.

```
   untrusted content ─► [ingest] ─►  ╔═══════════════╗  ◄─ gate verdicts
   agent lifecycle   ──────────────► ║  EVENT LOG    ║  ◄─ approver decisions
   tool observations ──────────────► ║ append-only,  ║
                                     ║ hash-chained  ║
                                     ╚══════╤════════╝
                                fold/replay │
              ┌───────────────┬─────────────┴──────────────┐
              ▼               ▼                             ▼
        Context Store     Audit View                 Step History
        (retrieval)    (who/what/why)              (the agent loop)
```

## What's built

- **Event log** (`src/tenet/events/`) — the spine. Envelope with ULID ids and a
  SHA-256 hash chain, a **closed event taxonomy**, an append-only
  `InMemoryEventLog`, and `replay`/`rebuild` folds. Tamper-evident: mutate any
  retained event and `verify()` raises. Detecting valid suffix truncation or
  whole-log replacement requires an independently retained external anchor.
- **Memory core on the log** (`src/tenet/memory/`) — `MemoryCore.ingest`
  appends a `memory.raw.appended` event (the source of truth) and folds it into
  the raw store and the context store. The context store is a *pure projection*
  (deterministic contextualizer, so no events of its own) and rebuilds
  identically from the log; retrieval is namespace-filter-first.
- **Scope grants** (`src/tenet/scope/`) — a `ScopeGrant` is the sole source of
  an agent's authority for one task (namespaces, tools, action budget, expiry).
  The retriever enforces its `namespaces`: retrieval can never reach memory
  outside the grant, and no retrieved content can widen it.
- **Governance gate — contract** (`src/tenet/gate/`) — the pure-function
  `DefaultGate`: grant check (tool membership, expiry, action budget) → policy →
  unmatched⇒DENY. Ships the `Gate`/`Policy` protocols and `Verdict`/
  `GateDecision`; the policy *rules* are authored separately (describe-first).
- **Agent loop + approver** (`src/tenet/agent/`, `src/tenet/approver/`) — the
  deterministic `ScriptedBrain` (observation-aware protocol), and `AgentLoop`: task →
  retrieve (as data) → propose → gate → approve/execute/block, every hop an
  event with an intact why-chain. Escalations fail closed; the approval surface
  labels agent-provided arguments and justification as untrusted, since they
  can relay memory content.
- **Executor + sandboxed fs tools** (`src/tenet/executor/`) — a closed tool
  registry over `fs.read`/`fs.write`/`fs.delete`, jailed to a sandbox root
  (`..`, absolute-path and symlink escapes all rejected on the resolved path).
  Logical `path_prefix` violations are blocked by the gate before dispatch.
  Grant constraints are re-checked at the executor — defense in
  depth; it does not trust that the gate was the only path to it.
- **Journal + reconciliation** (`src/tenet/journal.py`, `src/tenet/recovery.py`,
  A3) — `journal.append` writes a bounded report and its operation receipt in one
  SQLite transaction, bound to the dispatch's operation ID, proposal digest,
  grant and store identity. When a crash leaves a journal dispatch without an
  outcome, `reconcile_journal` (a trusted operator API, not a Brain tool)
  inspects the store: it records `action.reconciled` as *applied* if the report
  committed, or fences the operation as *cancelled* so a late worker can never
  apply it. It never retries the effect, refunds budget or reopens the task.
- **LLM Brain** (`src/tenet/agent/llm_brain.py`) — the same Brain interface
  backed by a model you run yourself, free: it speaks the OpenAI-compatible
  `/chat/completions` protocol with tool calling (Ollama by default; llama.cpp
  `llama-server`, LM Studio or vLLM by changing `base_url`), using only the
  standard library. The system message is fixed operator text; retrieved memory
  and earlier results travel only inside an escaped `<untrusted_data>` block in
  the user message, and the model must answer with exactly one tool call (one
  per proposable action, or `finish`); anything else aborts the task. It
  proposes; the grant and gate still decide. A fooled model is still stopped
  (tested with a scripted fake transport). To measure a real model:

  ```bash
  ollama pull qwen2.5:7b
  PYTHONPATH=src python evaluation/llm_headline.py --runs 10
  ```

  No live result is recorded yet.
- **Headline demo + audit view** (`src/tenet/demo/`, `src/tenet/projections/`) —
  a poisoned memory entry tells the agent to delete `/protected/secrets.txt`.
  The same deterministic, deliberately gullible Brain drives a naive RAG agent
  (which deletes it) and the Tenet loop (which retrieves it as data, proposes
  the delete, and gets DENY → `action.blocked` from the grant check). The audit
  view folds the log into a why-chain from each proposal back to the cited
  memory source. The demo policy is a labeled fixture, not production policy,
  and the Brain is not an LLM.

## Run it

```bash
pip install -e ".[dev]"      # numpy + pytest
pytest                       # event log + memory core (all green)
python -m tenet.demo         # headline demo: poisoned memory, naive vs. Tenet agent
python -m tenet.demo.memory_demo   # memory-core walkthrough: ranking + scope isolation
```

To run the context projection on Postgres + pgvector instead of in memory:

```bash
pip install -e ".[postgres,dev]"
export TENET_PG_DSN="host=localhost dbname=tenet user=tenet"   # needs CREATE EXTENSION vector
pytest tests/test_pgvector.py   # skipped when TENET_PG_DSN is unset
```

```python
from tenet import HashingEmbedder, MemoryCore
from tenet.memory.pgvector import PgVectorContextStore

store = PgVectorContextStore(dsn, dim=256, table="tenet_context")  # one table per log
memory = MemoryCore(HashingEmbedder(dim=256), context_store=store, log=durable_log)
```

The table is still a disposable projection: MemoryCore rebuilds it from the log
on start. Ranking is exact (no ANN index) so results match the in-memory store;
scores use pgvector's single precision and may differ by about 1e-7.

To serve the headline demo over HTTP (single principal, one bearer token):

```bash
pip install -e ".[api]"
TENET_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  uvicorn tenet.api.demo:app --factory
```

| Endpoint | Does | Evented as |
|---|---|---|
| `POST /memory` | ingest content (idempotent with `ingest_id`) | `memory.raw.appended` |
| `POST /tasks` | start one task under a fresh, single-use grant (202 + `task_id`) | the full §7 chain |
| `GET /tasks/{id}` | status, steps, and any pending approval surface | read-only |
| `POST /tasks/{id}/approvals/{proposal_ref}` | approve or reject an escalation, echoing its `binding` | `approval.decided` |
| `GET /tasks/{id}/audit` | readable why-chain | projection of the log |
| `GET /audit` | hash chain + structural audit | read-only |

A task request carries the task text and the grant only. The Brain is chosen by
whoever deploys the server, so callers cannot submit proposals; intent and
authority stay on separate paths. Tasks run in the background; an escalation
waits for the principal to answer with the binding of the approval surface
they were shown (a mismatch is refused with 409 and stays pending). No answer
before the timeout or the grant's expiry, or a server shutdown, is a deny.
Pending approvals live in process memory. Responses never include read file
content, the generated
`/docs`, `/redoc` and `/openapi.json` are disabled (they would bypass the token),
and the API is not multi-tenant.

No install? `PYTHONPATH=src` works too (the tests already put `src/` on the path).

```python
from tenet.events import InMemoryEventLog, Actor, taxonomy as T

log = InMemoryEventLog()
log.append(
    namespace="proj",
    actor=Actor(kind="user", id="alice"),
    event_type=T.TASK_INITIATED,
    payload={"description": "tidy the sandbox", "grant": {"tools": ["fs.read"]}},
    correlation_id="task-1",
)
log.verify()                 # True — the hash chain holds
```

## Build order (see CLAUDE.md §12)

1. **Event log** ✅
2. **Memory core on the log** (raw = event, context = projection) ✅
3. **ScopeGrant + retriever scope enforcement** ✅
4. **Governance gate — contract** ✅ (policy is Sai's, describe-first; loop wiring next)
5. **Agent loop + approver/escalate path** ✅
6. **Executor + sandboxed fs tools** ✅
7. **Headline demo** — poisoned corpus, naive agent vs. Tenet agent, audit view as
   the receipt ✅
8. **pgvector context store** ✅
9. **FastAPI surface** ✅

## What this defends against (and what it doesn't)

In scope for v1: prompt injection via memory, scope escalation, and
unattributable actions. **Out of scope:** host/process/db compromise, a
malicious principal, model-level jailbreaks of the agent's "Brain" (the gate
exists *because* the Brain can be fooled), and DoS. Data/instruction separation
*reduces* injection risk; it does not eliminate it. See CLAUDE.md §1.
