"""Tenet's governance gate — contract + pure mechanism (build step 4).

Ships the contract the rest of the system may assume: the ``Gate`` and
``Policy`` protocols, ``Verdict``/``GateDecision``, and ``DefaultGate`` (grant
check → policy → unmatched⇒DENY). The policy *content* (``gate/policy.py``) is
authored separately per D8 and is intentionally not part of this module.
"""

from __future__ import annotations

from .contract import DefaultGate, Gate, GateDecision, Policy, Verdict

__all__ = ["Gate", "DefaultGate", "Policy", "Verdict", "GateDecision"]
