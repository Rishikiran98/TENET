"""Runnable demo of the Tenet memory core.

Shows three things:
    1. Ingest across two scopes.
    2. Retrieval ranks by relevance within a scope.
    3. SCOPE ISOLATION: a query in scope A cannot surface a memory from scope B,
       even when that memory is the most relevant match overall. This is the
       seed of the security model the governance gate will build on.

Run:  python -m tenet.demo
"""

from __future__ import annotations

from tenet import HashingEmbedder, InMemoryStore, MemoryCore


def main() -> None:
    core = MemoryCore(embedder=HashingEmbedder(), store=InMemoryStore())

    # --- ingest -----------------------------------------------------------
    core.ingest(
        "The alpha project deploys to the us-east-1 region on Fridays.",
        scope="project-alpha", source="alpha/runbook.md",
    )
    core.ingest(
        "Alpha's database backups run nightly at 02:00 UTC.",
        scope="project-alpha", source="alpha/ops-notes.md",
    )
    core.ingest(
        "The beta project deploys to eu-west-1 and stores backups in Glacier.",
        scope="project-beta", source="beta/runbook.md",
    )

    # --- 2. relevance ranking within a scope ------------------------------
    print("Query: 'where does the project deploy?'  (scope = project-alpha)\n")
    for res in core.retrieve("where does the project deploy?", scope="project-alpha"):
        r = res.record
        print(f"  score={res.score:.3f}  [{r.source}]  {r.content}")

    # --- 3. scope isolation ----------------------------------------------
    # 'Glacier backups' lives ONLY in project-beta. Asking from project-alpha
    # must never surface it, no matter how relevant.
    print("\nQuery: 'backups in Glacier'  (scope = project-alpha)\n")
    results = core.retrieve("backups in Glacier", scope="project-alpha")
    for res in results:
        r = res.record
        print(f"  score={res.score:.3f}  [{r.source}]  {r.content}")
    leaked = any("beta" in res.record.scope for res in results)
    print(f"\n  beta memory leaked into alpha query? {leaked}  "
          f"(expected: False)")

    # Same query, correct scope — now it surfaces.
    print("\nQuery: 'backups in Glacier'  (scope = project-beta)\n")
    for res in core.retrieve("backups in Glacier", scope="project-beta"):
        r = res.record
        print(f"  score={res.score:.3f}  [{r.source}]  {r.content}")


if __name__ == "__main__":
    main()
