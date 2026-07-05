"""Tenet's agent layer. Only the Proposal DTO exists so far; the Brain and the
loop are later slices (§7)."""

from __future__ import annotations

from .proposal import Proposal

__all__ = ["Proposal"]
