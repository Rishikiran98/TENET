"""Poisoned memory, the same fooled Brain, two agents, one receipt.

Run:  python -m tenet.demo
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..events.audit import audit_trace
from ..projections import audit_view, render_audit
from .corpus import SECRET, TASK, load_corpus, make_sandbox
from .naive_agent import run_naive_agent
from .tenet_agent import run_tenet_agent


def _secret_state(root: Path) -> str:
    return "intact" if (root / SECRET).exists() else "DELETED"


def main() -> None:
    print(f"Task: {TASK!r}\nMemory corpus (namespace 'project'):")
    for source, content in load_corpus():
        print(f"  {source}: {' '.join(content.split())}")

    with tempfile.TemporaryDirectory() as naive_dir, tempfile.TemporaryDirectory() as tenet_dir:
        naive_root, tenet_root = make_sandbox(naive_dir), make_sandbox(tenet_dir)

        print("\n=== NAIVE RAG AGENT (no grant, no gate) ===")
        for tool, args, outcome in run_naive_agent(naive_root):
            print(f"  {tool} {args['path']}: {outcome}")
        print(f"  /{SECRET.as_posix()}: {_secret_state(naive_root)}")

        print("\n=== TENET AGENT (same Brain, same memory) ===")
        result, memory = run_tenet_agent(tenet_root)
        for step in result.steps:
            print(f"  {step.proposal.tool} {step.proposal.args['path']}: "
                  f"{step.decision.verdict.value.upper()} -> {step.outcome_event_type}")
        print(f"  /{SECRET.as_posix()}: {_secret_state(tenet_root)}")

        print("\n=== AUDIT VIEW (folded from the event log) ===")
        print(render_audit(audit_view(memory.log, result.task_id)))
        memory.log.verify()
        issues = audit_trace(memory.log).issues
        print(f"\nHash chain verified ({len(memory.log)} events); "
              f"structural audit issues: {len(issues)}")

    print("\nThe Brain was fooled in both runs. Only the grant and gate differ.\n"
          "Data/instruction separation reduces injection risk; it does not eliminate it.")


if __name__ == "__main__":
    main()
