"""External anchors (A4): a log that lost its tail or was replaced passes its
own hash-chain check, but not a check against an anchor kept elsewhere."""

import json
import sqlite3

import pytest

from tenet.events import (GENESIS_HASH, Actor, Anchor, InMemoryEventLog, SQLiteEventLog,
                          check_anchor, take_anchor, taxonomy as T)


def append(log, n, summary="done"):
    for i in range(n):
        log.append(namespace="n", actor=Actor("user", "u"), event_type=T.TASK_COMPLETED,
                   payload={"summary": f"{summary} {i}"}, correlation_id=f"t{i}")


def test_empty_log_anchors_at_genesis():
    log = InMemoryEventLog()
    anchor = take_anchor(log)
    assert anchor == Anchor(0, "", GENESIS_HASH)
    assert check_anchor(log, anchor).ok


def test_growth_after_the_anchor_is_fine():
    log = InMemoryEventLog()
    append(log, 3)
    anchor = take_anchor(log)
    append(log, 2, "later")
    check = check_anchor(log, anchor)
    assert check.ok and "2 events appended since" in check.reason


def test_truncated_tail_passes_the_chain_but_fails_the_anchor(tmp_path):
    """The headline case: deleting the newest rows leaves a valid prefix."""
    path = tmp_path / "events.db"
    log = SQLiteEventLog(path)
    append(log, 3)
    anchor = take_anchor(log)
    append(log, 2, "later")
    log.close()
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM events WHERE seq > 1")      # keep only the first event

    reopened = SQLiteEventLog(path)
    assert reopened.verify()                                # the chain alone cannot tell
    check = check_anchor(reopened, anchor)
    assert not check.ok and check.reason == "log shorter than anchor: 2 events missing"
    reopened.close()


def test_replaced_log_fails_even_at_the_same_length():
    original, forged = InMemoryEventLog(), InMemoryEventLog()
    append(original, 3)
    append(forged, 3, "forged")
    anchor = take_anchor(original)
    assert forged.verify()
    check = check_anchor(forged, anchor)
    assert not check.ok and check.reason == "history replaced at or before the anchor"


def test_edited_history_reports_the_broken_chain(tmp_path):
    path = tmp_path / "events.db"
    log = SQLiteEventLog(path)
    append(log, 2)
    anchor = take_anchor(log)
    with sqlite3.connect(path) as db:
        document = json.loads(db.execute("SELECT document FROM events WHERE seq = 1").fetchone()[0])
        document["payload"]["summary"] = "tampered"
        db.execute("UPDATE events SET document = ? WHERE seq = 1", (json.dumps(document),))
    check = check_anchor(log, anchor)
    assert not check.ok and check.reason.startswith("chain broken:")
    log.close()


def test_round_trip_and_strict_parsing():
    log = InMemoryEventLog()
    append(log, 1)
    anchor = take_anchor(log)
    assert Anchor.from_dict(anchor.to_dict()) == anchor
    good = anchor.to_dict()
    for bad in [
        {**good, "events": True},
        {**good, "events": -1},
        {**good, "events": 1.0},
        {**good, "head_hash": "ABC"},
        {**good, "head_hash": "G" * 64},
        {**good, "event_id": ""},                              # id required when events > 0
        {"events": 0, "event_id": "x", "head_hash": GENESIS_HASH},
        {"events": 0, "event_id": "", "head_hash": "1" * 64},  # empty log must be genesis
        {**good, "extra": 1},
        {k: v for k, v in good.items() if k != "event_id"},
        "not a dict",
    ]:
        with pytest.raises(ValueError):
            Anchor.from_dict(bad)


# -- HTTP ------------------------------------------------------------------

def test_http_anchor_round_trip_and_mismatch(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from test_api import AUTH, _client

    client, memory, _ = _client(tmp_path)
    anchor = client.get("/audit/anchor", headers=AUTH)
    assert anchor.status_code == 200 and anchor.json()["events"] == len(memory.log)

    verified = client.post("/audit/anchor/verify", headers=AUTH, json=anchor.json())
    assert verified.status_code == 200 and verified.json()["ok"] is True

    longer = {**anchor.json(), "events": anchor.json()["events"] + 5}
    missing = client.post("/audit/anchor/verify", headers=AUTH, json=longer)
    assert missing.status_code == 200
    assert missing.json() == {"ok": False, "reason": "log shorter than anchor: 5 events missing"}


def test_http_rejects_malformed_anchors_and_missing_tokens(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from test_api import AUTH, _client

    client, _, _ = _client(tmp_path)
    for body in ({"events": -1, "event_id": "", "head_hash": GENESIS_HASH},
                 {"events": 0, "event_id": "", "head_hash": "nope"},
                 {"events": 0, "event_id": "", "head_hash": GENESIS_HASH, "x": 1}):
        assert client.post("/audit/anchor/verify", headers=AUTH, json=body).status_code == 422
    assert client.get("/audit/anchor").status_code == 401
    assert client.post("/audit/anchor/verify",
                       json={"events": 0, "event_id": "", "head_hash": GENESIS_HASH}).status_code == 401


def test_a_broken_chain_is_never_anchored(tmp_path):
    import dataclasses
    from tenet.events import ChainError

    log = InMemoryEventLog()
    append(log, 2)
    log._events[0] = dataclasses.replace(log._events[0], namespace="evil")
    with pytest.raises(ChainError):
        take_anchor(log)


def test_http_anchor_of_a_broken_chain_is_409(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import dataclasses
    from test_api import AUTH, _client

    client, memory, _ = _client(tmp_path)
    memory.log._events[0] = dataclasses.replace(memory.log._events[0], namespace="evil")
    response = client.get("/audit/anchor", headers=AUTH)
    assert response.status_code == 409 and response.json()["chain_verified"] is False
