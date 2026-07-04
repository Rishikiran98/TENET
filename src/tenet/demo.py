"""Runnable demo of the event-sourced, scope-bound memory core.

Shows five things:
    1. Ingest across two namespaces — each ingest appends a `memory.raw.appended`
       event to the hash-chained log (the source of truth).
    2. Retrieval ranks by relevance, but only within what the GRANT permits.
    3. GRANT ENFORCEMENT: a grant for project-alpha physically cannot retrieve
       project-beta memory, even when beta is the best match overall. Authority
       comes only from the grant — no memory content can widen it.
    4. A grant that includes beta CAN read beta.
    5. PROJECTIONS: drop the context store and rebuild it purely by replaying the
       log — retrieval is identical, and the log verifies as untampered.

Run:  python -m tenet.demo
"""

from __future__ import annotations

from tenet import HashingEmbedder, MemoryCore
from tenet.scope import ScopeGrant


def _show(core: MemoryCore, grant: ScopeGrant, query: str, ns: str | None = None) -> None:
    label = ns if ns is not None else f"any of {sorted(grant.namespaces)}"
    print(f"Query: {query!r}  (grant: {sorted(grant.namespaces)}, asking: {label})\n")
    results = core.retrieve(query, grant, namespace=ns)
    if not results:
        print("  (nothing — denied or empty)")
    for res in results:
        r = res.record
        print(f"  score={res.score:.3f}  [{r.namespace}: {r.source}]  {r.text}")


def main() -> None:
    core = MemoryCore(embedder=HashingEmbedder())

    core.ingest("The alpha project deploys to us-east-1 on Fridays.",
                namespace="project-alpha", source="alpha/runbook.md")
    core.ingest("Alpha's database backups run nightly at 02:00 UTC.",
                namespace="project-alpha", source="alpha/ops-notes.md")
    core.ingest("The beta project deploys to eu-west-1 and stores backups in Glacier.",
                namespace="project-beta", source="beta/runbook.md")
    print(f"Ingested 3 records → {len(core.log)} events "
          f"(chain verifies: {core.log.verify()})\n")

    alpha_grant = ScopeGrant.issue(
        principal="alice", task_id="task-1", namespaces={"project-alpha"})
    both_grant = ScopeGrant.issue(
        principal="alice", task_id="task-2", namespaces={"project-alpha", "project-beta"})

    # 2. ranking within the grant
    _show(core, alpha_grant, "where does the project deploy?", ns="project-alpha")

    # 3. grant enforcement: alpha grant cannot reach beta's Glacier memory
    print()
    _show(core, alpha_grant, "backups in Glacier", ns="project-beta")

    # 4. a grant that includes beta can read it
    print()
    _show(core, both_grant, "backups in Glacier")

    # 5. context store is a projection
    before = [(r.record.context_id, round(r.score, 6))
              for r in core.retrieve("deploy", both_grant)]
    core.rebuild_projections()
    after = [(r.record.context_id, round(r.score, 6))
             for r in core.retrieve("deploy", both_grant)]
    print(f"\nRebuilt context store from the log. Retrieval identical? "
          f"{before == after}  (expected: True)")


if __name__ == "__main__":
    main()
