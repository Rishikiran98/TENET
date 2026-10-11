"""A conventional RAG agent: retrieve, let the Brain propose, execute.

There is no grant, gate, or approval step, and nothing is evented beyond the
memory ingest. The file tools still run inside the sandbox directory, so the
demo cannot touch the host — but inside it, whatever the Brain proposes runs.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts import Observation, ToolExecutionError
from ..executor.fs_tools import FsDelete, FsRead, FsWrite, SandboxedFs
from ..memory import HashingEmbedder, MemoryCore
from ..scope import ScopeGrant
from .brain import GullibleBrain
from .corpus import NAMESPACE, TASK, load_corpus


def run_naive_agent(sandbox: str | Path, *, task: str = TASK,
                    max_steps: int = 20) -> list[tuple[str, dict, str]]:
    """Run the task and return (tool, args, outcome) for every proposal."""
    memory = MemoryCore(embedder=HashingEmbedder())
    for source, content in load_corpus():
        memory.ingest(content, namespace=NAMESPACE, source=source)
    # Retrieval itself is identical; a conventional agent simply reads it all.
    everything = ScopeGrant.issue(principal="naive", task_id="naive-task",
                                  namespaces={NAMESPACE})
    context = memory.retrieve(task, everything)

    fs = SandboxedFs(sandbox)
    tools = {tool.name: tool for tool in (FsRead(fs), FsWrite(fs), FsDelete(fs))}
    brain, observations, actions = GullibleBrain(), [], []
    for _ in range(max_steps):
        proposal = brain.propose(task, context, observations=tuple(observations))
        if proposal is None:
            break
        try:
            result = tools[proposal.tool].execute(proposal.args, {})
            outcome = "executed"
        except ToolExecutionError as exc:
            result, outcome = None, f"failed: {exc}"
        actions.append((proposal.tool, dict(proposal.args), outcome))
        observations.append(Observation(proposal, "", outcome, result))
    return actions
