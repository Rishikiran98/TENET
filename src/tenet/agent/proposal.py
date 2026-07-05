"""The Proposal — the Brain's intent for a single action (§7).

Data only, no authority. A proposal carries *what the agent wants to do* and
*why*; it never carries capability. It travels on a separate code path from the
ScopeGrant and the two meet only inside the gate — intent on one path, authority
on the other (§5, §10). Mirrors the ``agent.action.proposed`` event payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Proposal:
    tool: str                                  # e.g. "fs.read" — must be a grant tool
    args: dict = field(default_factory=dict)   # tool arguments
    justification: str = ""                    # why the Brain proposes this
    cited_context_ids: tuple[str, ...] = ()    # provenance: context that motivated it
