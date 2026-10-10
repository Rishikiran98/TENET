"""The scope and authorization model (§5, D3).

Scope stops being a string. Authority is a **capability grant** issued by the
principal at task initiation, carried in the ``task.initiated`` event, and
checked wherever authority is exercised. Two properties make it defensible:

- **Authority flows only from the grant.** Nothing retrieved from memory can
  widen it — retrieved content is *data*, and data cannot carry capability. The
  grant and the agent's intent travel on separate code paths and meet only at an
  enforcement point (cf. CaMeL).
- **No ambient authority.** Grants are task-scoped (``task_id``), expire
  (``expires_at``), and default-deny: a namespace or tool absent from the grant
  is forbidden, not "unspecified."

Two enforcement points, one grant (invariant 3):

- the **retriever** enforces ``namespaces`` (built here, step 3);
- the **gate** enforces ``tools``, ``constraints``, ``max_actions``,
  ``expires_at`` (step 4+). The helpers those need — ``tool_grant``,
  ``is_expired`` — live on the grant so the gate consumes them without
  re-deriving policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from collections.abc import Mapping

from ..events import new_ulid
from ..values import freeze, thaw


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ToolGrant:
    """A single capability: one tool, plus the constraints under which it may be
    used. Constraints are the gate's to interpret (e.g. a path prefix); the grant
    only carries them."""

    tool: str                       # e.g. "fs.read"
    constraints: dict = field(default_factory=dict)  # e.g. {"path_prefix": "/sandbox"}

    def __post_init__(self):
        if not isinstance(self.tool, str) or not self.tool:
            raise ValueError('tool name must be non-empty')
        if not isinstance(self.constraints, Mapping):
            raise ValueError('constraints must be a mapping')
        object.__setattr__(self, 'constraints', freeze(self.constraints))


@dataclass(frozen=True)
class ScopeGrant:
    """The authority for exactly one task. Immutable and non-reusable."""

    grant_id: str
    principal: str                  # who granted
    task_id: str                    # binds the grant to one task — no reuse
    namespaces: frozenset[str]      # readable memory namespaces
    tools: tuple[ToolGrant, ...]    # closed tool allowlist
    max_actions: int                # circuit breaker (enforced by the gate)
    expires_at: str                 # ISO-8601 UTC wall-clock bound (gate-enforced)

    def __post_init__(self):
        for value in (self.grant_id, self.principal, self.task_id):
            if not isinstance(value, str) or not value:
                raise ValueError('grant identifiers must be non-empty strings')
        if type(self.max_actions) is not int or self.max_actions < 0:
            raise ValueError('max_actions must be a nonnegative integer')
        if isinstance(self.namespaces, str):
            raise ValueError('namespaces must be a collection')
        object.__setattr__(self, 'namespaces', frozenset(self.namespaces))
        object.__setattr__(self, 'tools', tuple(self.tools))
        if any(not isinstance(n, str) or not n for n in self.namespaces):
            raise ValueError('invalid namespace')
        if any(not isinstance(t, ToolGrant) for t in self.tools):
            raise ValueError('invalid tool grant')
        if len({t.tool for t in self.tools}) != len(self.tools):
            raise ValueError('duplicate tool grant')
        try:
            expiry = datetime.fromisoformat(self.expires_at)
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid expiry') from exc
        if expiry.tzinfo is None:
            raise ValueError('expiry must include timezone')

    @classmethod
    def issue(
        cls,
        *,
        principal: str,
        task_id: str,
        namespaces,
        tools=(),
        max_actions: int = 10,
        ttl_seconds: int = 3600,
    ) -> "ScopeGrant":
        """Convenience constructor: mint a grant id and derive ``expires_at`` from
        a TTL. The principal issues this at task initiation."""
        return cls(
            grant_id=new_ulid(),
            principal=principal,
            task_id=task_id,
            namespaces=namespaces,
            tools=tuple(tools),
            max_actions=max_actions,
            expires_at=(_now() + timedelta(seconds=ttl_seconds)).isoformat(),
        )

    # -- retriever enforcement (step 3) ------------------------------------
    def can_read(self, namespace: str) -> bool:
        """Default-deny: only namespaces explicitly in the grant are readable."""
        return namespace in self.namespaces

    # -- gate enforcement helpers (consumed in step 4+) --------------------
    def tool_grant(self, tool: str) -> ToolGrant | None:
        """The ToolGrant for ``tool``, or None if the grant does not confer it
        (default-deny). The gate uses this; the retriever never does."""
        for tg in self.tools:
            if tg.tool == tool:
                return tg
        return None

    def allows_tool(self, tool: str) -> bool:
        return self.tool_grant(tool) is not None

    def is_expired(self, now: datetime | None = None) -> bool:
        """Whether the wall-clock bound has passed. Checked by the gate before any
        action and by the retriever before reads. Equality is expired."""
        moment = now if now is not None else _now()
        if moment.tzinfo is None:
            raise ValueError('clock must include timezone')
        return moment >= datetime.fromisoformat(self.expires_at)

    def to_payload(self) -> dict:
        """JSON-serializable form, carried inline in the ``task.initiated`` event
        (§5: the grant travels with the task, full and auditable). Sorted so the
        canonical event hash is deterministic."""
        return {
            "grant_id": self.grant_id,
            "principal": self.principal,
            "task_id": self.task_id,
            "namespaces": sorted(self.namespaces),
            "tools": [
                {"tool": tg.tool, "constraints": thaw(tg.constraints)} for tg in self.tools
            ],
            "max_actions": self.max_actions,
            "expires_at": self.expires_at,
        }
