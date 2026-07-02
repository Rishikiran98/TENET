"""Runnable demo of the event-sourced memory core.

Shows four things:
    1. Ingest across two namespaces — each ingest appends a `memory.raw.appended`
       event to the hash-chained log (the source of truth).
    2. Retrieval ranks by relevance within a namespace.
    3. NAMESPACE ISOLATION: a query in namespace A cannot surface memory from
       namespace B, even when that memory is the most relevant match overall.
    4. PROJECTIONS: drop the context store and rebuild it purely by replaying the
       log — retrieval is identical, and the log verifies as untampered.

Run:  python -m tenet.demo
"""

from __future__ import annotations

from tenet import HashingEmbedder, MemoryCore


def _show(core: MemoryCore, query: str, namespace: str) -> None:
    print(f"Query: {query!r}  (namespace = {namespace})\n")
    for res in core.retrieve(query, namespace=namespace):
        r = res.record
        print(f"  score={res.score:.3f}  [{r.source}]  {r.text}")


def main() -> None:
    core = MemoryCore(embedder=HashingEmbedder())

    # --- ingest (each append is an event) ---------------------------------
    core.ingest(
        "The alpha project deploys to the us-east-1 region on Fridays.",
        namespace="project-alpha", source="alpha/runbook.md",
    )
    core.ingest(
        "Alpha's database backups run nightly at 02:00 UTC.",
        namespace="project-alpha", source="alpha/ops-notes.md",
    )
    core.ingest(
        "The beta project deploys to eu-west-1 and stores backups in Glacier.",
        namespace="project-beta", source="beta/runbook.md",
    )
    print(f"Ingested 3 records → {len(core.log)} events in the log "
          f"(chain verifies: {core.log.verify()})\n")

    # --- 2. relevance ranking within a namespace --------------------------
    _show(core, "where does the project deploy?", "project-alpha")

    # --- 3. namespace isolation -------------------------------------------
    # 'Glacier backups' lives ONLY in project-beta. Asking from project-alpha
    # must never surface it, no matter how relevant.
    print()
    results = core.retrieve("backups in Glacier", namespace="project-alpha")
    _show(core, "backups in Glacier", "project-alpha")
    leaked = any("beta" in res.record.namespace for res in results)
    print(f"\n  beta memory leaked into alpha query? {leaked}  (expected: False)\n")

    # Same query, correct namespace — now it surfaces.
    _show(core, "backups in Glacier", "project-beta")

    # --- 4. context store is a projection ---------------------------------
    before = [(r.record.context_id, round(r.score, 3))
              for r in core.retrieve("where does the project deploy?", namespace="project-alpha")]
    core.rebuild_projections()   # drop + re-fold straight from the verified log
    after = [(r.record.context_id, round(r.score, 3))
             for r in core.retrieve("where does the project deploy?", namespace="project-alpha")]
    print(f"\nRebuilt context store from the log. Retrieval identical? "
          f"{before == after}  (expected: True)")


if __name__ == "__main__":
    main()
