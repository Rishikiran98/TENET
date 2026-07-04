"""Tenet's scope and authorization model (build step 3).

``ScopeGrant`` is the sole source of an agent's authority for one task. The
retriever enforces its ``namespaces``; the gate (later) enforces its tools,
constraints, action budget, and expiry.
"""

from __future__ import annotations

from .grant import ScopeGrant, ToolGrant

__all__ = ["ScopeGrant", "ToolGrant"]
