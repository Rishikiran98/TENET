"""The closed event taxonomy (D1).

Every event that may enter the log has a type drawn from this set and nothing
else. A closed taxonomy is what makes the audit trail legible: every fold in
``replay.py`` can enumerate exactly the events it cares about, and an event of
an unknown type is rejected at the boundary rather than discovered three
projections downstream.

Note on the count: the decision register calls this the "12-type" taxonomy,
counting ``task.completed`` / ``task.aborted`` as one row of the table in
CLAUDE.md §3.2. As distinct ``event_type`` strings there are 13 — all are
enumerated below.
"""

from __future__ import annotations

# --- memory ---------------------------------------------------------------
MEMORY_RAW_APPENDED = "memory.raw.appended"
MEMORY_CONTEXT_DERIVED = "memory.context.derived"

# --- task lifecycle -------------------------------------------------------
TASK_INITIATED = "task.initiated"
TASK_COMPLETED = "task.completed"
TASK_ABORTED = "task.aborted"

# --- agent + gate ---------------------------------------------------------
AGENT_RETRIEVAL_PERFORMED = "agent.retrieval.performed"
AGENT_ACTION_PROPOSED = "agent.action.proposed"
GATE_VERDICT_ISSUED = "gate.verdict.issued"

# --- approval -------------------------------------------------------------
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_DECIDED = "approval.decided"

# --- execution ------------------------------------------------------------
ACTION_EXECUTED = "action.executed"
ACTION_BLOCKED = "action.blocked"
ACTION_FAILED = "action.failed"

# event_type -> payload keys that MUST be present. This keeps the closed
# taxonomy meaningful: a known type carrying a malformed payload is rejected at
# append time. Essentials only (from the §3.2 table); payloads may carry more.
REQUIRED_PAYLOAD_KEYS: dict[str, frozenset[str]] = {
    MEMORY_RAW_APPENDED: frozenset({"content", "content_hash", "source"}),
    MEMORY_CONTEXT_DERIVED: frozenset({"raw_id", "derived_text", "contextualizer_version"}),
    TASK_INITIATED: frozenset({"description", "grant"}),
    TASK_COMPLETED: frozenset({"summary"}),
    TASK_ABORTED: frozenset({"reason"}),
    AGENT_RETRIEVAL_PERFORMED: frozenset({"query", "scope", "context_ids"}),
    AGENT_ACTION_PROPOSED: frozenset({"tool", "args", "justification", "cited_context_ids"}),
    GATE_VERDICT_ISSUED: frozenset({"verdict", "policy_version", "matched_rule_ids", "rationale"}),
    APPROVAL_REQUESTED: frozenset({"proposal_ref"}),
    APPROVAL_DECIDED: frozenset({"decision", "approver_id"}),
    ACTION_EXECUTED: frozenset({"result", "exit_status"}),
    ACTION_BLOCKED: frozenset({"proposal_ref", "verdict_ref"}),
    ACTION_FAILED: frozenset({"error"}),
}

# The closed set. Membership is derived from the required-keys map so the two
# can never drift apart.
TAXONOMY: frozenset[str] = frozenset(REQUIRED_PAYLOAD_KEYS)


def is_known(event_type: str) -> bool:
    return event_type in TAXONOMY


def validate_payload(event_type: str, payload: dict) -> None:
    """Raise ``ValueError`` unless ``event_type`` is in the closed taxonomy and
    ``payload`` carries at least its required keys."""
    if event_type not in TAXONOMY:
        raise ValueError(
            f"unknown event_type {event_type!r}: not in the closed taxonomy"
        )
    if not isinstance(payload, dict):
        raise ValueError(f"{event_type} payload must be a dict, got {type(payload).__name__}")
    missing = REQUIRED_PAYLOAD_KEYS[event_type] - payload.keys()
    if missing:
        raise ValueError(
            f"{event_type} payload missing required keys: {sorted(missing)}"
        )
