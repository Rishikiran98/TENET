# TENET

A secure semantic memory layer for AI agents, with **governance as a
first-class property** — demonstrated by one thin action agent built on top of
it. v1 proves a principle, *governed memory*, not a hardened security product.

The full design contract — threat model, architecture, decision register, and
build order — lives in [CLAUDE.md](CLAUDE.md). Read it before changing anything.

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
  past event and `verify()` raises.
- **Memory core on the log** (`src/tenet/memory/`) — `MemoryCore.ingest`
  appends a `memory.raw.appended` event (the source of truth) and folds it into
  the raw store and the context store. The context store is a *pure projection*
  (deterministic contextualizer, so no events of its own) and rebuilds
  identically from the log; retrieval is namespace-filter-first.

## Run it

```bash
pip install -e ".[dev]"      # numpy + pytest
pytest                       # event log + memory core (all green)
python -m tenet.demo         # memory-core walkthrough: ranking + scope isolation
```

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
3. ScopeGrant + retriever scope enforcement
4. Governance gate wired into the loop
5. Approver + escalate path
6. Executor + sandboxed fs tools
7. Headline demo — poisoned corpus, naive agent vs. Tenet agent, audit view as
   the receipt
8. Then pgvector · 9. Then FastAPI

## What this defends against (and what it doesn't)

In scope for v1: prompt injection via memory, scope escalation, and
unattributable actions. **Out of scope:** host/process/db compromise, a
malicious principal, model-level jailbreaks of the agent's "Brain" (the gate
exists *because* the Brain can be fooled), and DoS. Data/instruction separation
*reduces* injection risk; it does not eliminate it. See CLAUDE.md §1.
