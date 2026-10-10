"""The Proposal — the Brain's intent for a single action (§7).

Data only, no authority. A proposal carries *what the agent wants to do* and
*why*; it never carries capability. It travels on a separate code path from the
ScopeGrant and the two meet only inside the gate — intent on one path, authority
on the other (§5, §10). Mirrors the ``agent.action.proposed`` event payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from ..values import freeze, digest


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
