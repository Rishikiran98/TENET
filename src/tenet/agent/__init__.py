"""Tenet's agent layer (build steps 4-5): intent, the deterministic Brain, and
the loop that ties memory → Brain → gate → approver → executor together with
every hop evented."""

from __future__ import annotations

from .brain import Brain, ScriptedBrain
from .claude_brain import BrainError, ClaudeBrain
from .loop import AgentLoop, Executor, StepOutcome, TaskResult
from ..contracts import Proposal, ToolExecutionError, Observation

__all__ = [
    "Proposal",
    "Observation",
    "Brain",
    "ScriptedBrain",
    "ClaudeBrain",
    "BrainError",
    "AgentLoop",
    "Executor",
    "TaskResult",
    "StepOutcome",
    "ToolExecutionError",
]
