"""The closed tool registry and the executor the loop drives (§8).

A tool absent from the registry cannot run — the registry is a closed set, the
executor's analogue of the closed event taxonomy. ``RegistryExecutor``
implements the loop's ``Executor`` protocol; it is only ever driven by live
gate verdicts, never by replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from ..agent.loop import ToolExecutionError
from ..agent.proposal import Proposal
from ..scope import ToolGrant
from .fs_tools import FsDelete, FsRead, FsWrite, SandboxedFs


@runtime_checkable
class Tool(Protocol):
    name: str

    def execute(self, args: dict, constraints: dict) -> dict: ...


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered (closed set)")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)


class RegistryExecutor:
    """The loop's Executor: closed-registry lookup, then the tool runs with the
    grant's constraints — which the tool re-checks itself (defense in depth)."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, proposal: Proposal, tool_grant: ToolGrant) -> dict:
        tool = self._registry.get(proposal.tool)
        if tool is None:
            raise ToolExecutionError(
                f"tool {proposal.tool!r} not in the closed registry"
            )
        return tool.execute(proposal.args, tool_grant.constraints)


def fs_executor(sandbox_root: str | Path) -> RegistryExecutor:
    """The v1 executor: the three file tools, jailed to ``sandbox_root``."""
    fs = SandboxedFs(sandbox_root)
    registry = ToolRegistry()
    for tool in (FsRead(fs), FsWrite(fs), FsDelete(fs)):
        registry.register(tool)
    return RegistryExecutor(registry)
