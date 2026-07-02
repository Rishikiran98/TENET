"""Tests for the event log (build step 1).

The load-bearing assertions here are the security ones: the closed taxonomy is
actually closed, and the hash chain actually detects tampering. Everything else
in Tenet folds over this log, so if these break, the audit story breaks with it.
"""

import dataclasses

import pytest

from tenet.events import (
    Actor,
    GENESIS_HASH,
    InMemoryEventLog,
    ChainError,
    rebuild,
    replay,
    taxonomy as T,
)

USER = Actor(kind="user", id="alice")
AGENT = Actor(kind="agent", id="tenet-agent")
GATE = Actor(kind="gate", id="gate")


def _task_initiated(log, corr="task-1"):
    return log.append(
        namespace="proj",
        actor=USER,
        event_type=T.TASK_INITIATED,
        payload={"description": "tidy the sandbox", "grant": {"tools": ["fs.read"]}},
        correlation_id=corr,
    )


def test_append_assigns_envelope_and_chains_from_genesis():
    log = InMemoryEventLog()
    first = _task_initiated(log)
    second = log.append(
        namespace="proj",
        actor=AGENT,
        event_type=T.AGENT_RETRIEVAL_PERFORMED,
        payload={"query": "where are backups", "scope": "proj", "context_ids": []},
        correlation_id="task-1",
        causation_id=first.event_id,
    )

    # Envelope populated at append.
    assert len(first.event_id) == 26 and first.event_id.isupper() is not False
    assert first.ts and first.hash
    # Chain: first links to genesis, second links to first.
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert log.head_hash() == second.hash
    # Why-chain preserved.
    assert second.causation_id == first.event_id
    assert log.verify() is True


def test_unknown_event_type_is_rejected():
    log = InMemoryEventLog()
    with pytest.raises(ValueError, match="closed taxonomy"):
        log.append(
            namespace="proj",
            actor=AGENT,
            event_type="memory.raw.deleted",  # not in the closed set
            payload={"whatever": 1},
            correlation_id="task-1",
        )
    assert len(log) == 0  # nothing entered the log


def test_missing_required_payload_key_is_rejected():
    log = InMemoryEventLog()
    with pytest.raises(ValueError, match="missing required keys"):
        log.append(
            namespace="proj",
            actor=GATE,
            event_type=T.GATE_VERDICT_ISSUED,
            payload={"verdict": "deny"},  # missing policy_version, matched_rule_ids, rationale
            correlation_id="task-1",
        )
    assert len(log) == 0


def test_namespace_is_required_on_every_event():
    log = InMemoryEventLog()
    with pytest.raises(ValueError, match="namespace"):
        log.append(
            namespace="",
            actor=USER,
            event_type=T.TASK_COMPLETED,
            payload={"summary": "done"},
            correlation_id="task-1",
        )


def test_verify_detects_tampering():
    log = InMemoryEventLog()
    _task_initiated(log)
    log.append(
        namespace="proj",
        actor=AGENT,
        event_type=T.ACTION_BLOCKED,
        payload={"proposal_ref": "x", "verdict_ref": "y"},
        correlation_id="task-1",
    )
    assert log.verify() is True

    # Mutate a past event's payload in place while keeping its stored hash — the
    # exact move the chain exists to catch. Event is frozen, so rebuild it.
    tampered = dataclasses.replace(
        log._events[0],
        payload={"description": "exfiltrate secrets", "grant": {"tools": ["fs.read"]}},
    )
    log._events[0] = tampered

    with pytest.raises(ChainError):
        log.verify()


def test_replay_folds_deterministically_and_rebuild_verifies():
    log = InMemoryEventLog()
    _task_initiated(log)
    log.append(
        namespace="proj", actor=AGENT, event_type=T.AGENT_ACTION_PROPOSED,
        payload={"tool": "fs.read", "args": {"path": "notes.md"},
                 "justification": "task asks for it", "cited_context_ids": []},
        correlation_id="task-1",
    )
    log.append(
        namespace="proj", actor=GATE, event_type=T.GATE_VERDICT_ISSUED,
        payload={"verdict": "allow", "policy_version": "v1",
                 "matched_rule_ids": ["r1"], "rationale": "within grant"},
        correlation_id="task-1",
    )

    def count_by_type(state: dict, event) -> dict:
        return {**state, event.event_type: state.get(event.event_type, 0) + 1}

    counts = rebuild(log, count_by_type, {})
    assert counts == {
        T.TASK_INITIATED: 1,
        T.AGENT_ACTION_PROPOSED: 1,
        T.GATE_VERDICT_ISSUED: 1,
    }
    # replay over the raw iterator is equivalent (rebuild just verifies first).
    assert replay(iter(log), count_by_type, {}) == counts


def test_event_ids_are_unique():
    log = InMemoryEventLog()
    for _ in range(50):
        log.append(
            namespace="proj", actor=USER, event_type=T.TASK_COMPLETED,
            payload={"summary": "ok"}, correlation_id="task-1",
        )
    ids = [e.event_id for e in log]
    assert len(set(ids)) == len(ids)
