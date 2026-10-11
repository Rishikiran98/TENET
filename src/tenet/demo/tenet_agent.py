"""The same Brain and corpus, run through Tenet's governed loop."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..agent import AgentLoop, TaskResult
from ..approver import FailSafeDenyApprover
from ..executor import fs_executor
from ..gate import DefaultGate
from ..memory import HashingEmbedder, MemoryCore
from ..scope import ScopeGrant, ToolGrant
from .brain import GullibleBrain
from .corpus import NAMESPACE, TASK, load_corpus
from .policy import DemoFixturePolicy


def demo_grant(task_id: str = "demo-task") -> ScopeGrant:
    """What the principal authorizes: read project memory and project files."""
    return ScopeGrant.issue(
        principal="alice", task_id=task_id, namespaces={NAMESPACE},
        tools=(ToolGrant("fs.read", {"path_prefix": "/project"}),), max_actions=3,
    )


def run_tenet_agent(
    sandbox: str | Path, *, task: str = TASK,
    extra_records: Iterable[tuple[str, str]] = (), context_store=None,
) -> tuple[TaskResult, MemoryCore]:
    """Run the task under a grant. Returns the result and the memory (its log).
    Pass ``context_store`` (e.g. a ``PgVectorContextStore``) to swap the backend."""
    memory = MemoryCore(embedder=HashingEmbedder(), context_store=context_store)
    for source, content in [*load_corpus(), *extra_records]:
        memory.ingest(content, namespace=NAMESPACE, source=source)
    loop = AgentLoop(memory=memory, gate=DefaultGate(), policy=DemoFixturePolicy(),
                     approver=FailSafeDenyApprover(), executor=fs_executor(sandbox))
    result = loop.run_task(task=task, grant=demo_grant(), brain=GullibleBrain(),
                           namespace=NAMESPACE)
    return result, memory
