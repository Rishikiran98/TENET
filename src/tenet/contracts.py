"""Neutral intent and execution-error contracts, independent of agent control flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from .values import freeze, digest


@dataclass(frozen=True)
class Proposal:
    tool: str                                  # e.g. "fs.read" — must be a grant tool
    args: dict = field(default_factory=dict)   # tool arguments
    justification: str = ""                    # why the Brain proposes this
    cited_context_ids: tuple[str, ...] = ()    # provenance: context that motivated it

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError('proposal tool must be non-empty')
        if not isinstance(self.args, Mapping) or not isinstance(self.justification, str):
            raise ValueError('invalid proposal arguments or justification')
        if isinstance(self.cited_context_ids, str) or any(not isinstance(c, str) or not c for c in self.cited_context_ids):
            raise ValueError('invalid citations')
        object.__setattr__(self, 'args', freeze(self.args))
        object.__setattr__(self, 'cited_context_ids', tuple(self.cited_context_ids))

    @property
    def digest(self):
        return digest({'tool': self.tool, 'args': self.args,
                       'justification': self.justification,
                       'cited_context_ids': self.cited_context_ids})


class ToolExecutionError(Exception):
    """Known execution failure, optionally with an uncertain external effect."""

    def __init__(self, message, *, outcome_unknown=False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown
