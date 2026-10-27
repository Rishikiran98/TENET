"""External anchors: detect a log that lost its tail or was replaced (A4).

The hash chain makes edits to history evident, but a log can still lose its
newest events, or be swapped for a different, internally valid chain, without
breaking a single link. An *anchor* closes that gap: a tiny record of how long
the log was and what its head was at some moment. Kept somewhere the log's host
cannot rewrite, it lets a later check prove the log still contains that history.

An anchor proves nothing about events appended after it, and it is only as
trustworthy as the place it is stored. Anchor as often as you need.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .envelope import GENESIS_HASH
from .log import ChainError

_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Anchor:
    events: int        # how many events the log held when anchored
    event_id: str      # id of the last of those events ("" for an empty log)
    head_hash: str     # hash of that event (GENESIS_HASH for an empty log)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value) -> "Anchor":
        """Strict: an anchor comes from outside the host, so check every field."""
        if not isinstance(value, dict) or set(value) != {"events", "event_id", "head_hash"}:
            raise ValueError("anchor must have exactly events, event_id and head_hash")
        events, event_id, head_hash = value["events"], value["event_id"], value["head_hash"]
        if type(events) is not int or events < 0:
            raise ValueError("anchor events must be a non-negative integer")
        if not isinstance(head_hash, str) or _HASH.fullmatch(head_hash) is None:
            raise ValueError("anchor head_hash must be 64 lowercase hex characters")
        if not isinstance(event_id, str) or (event_id == "") != (events == 0):
            raise ValueError("anchor event_id must be empty exactly when events is 0")
        if events == 0 and head_hash != GENESIS_HASH:
            raise ValueError("an empty-log anchor must carry the genesis hash")
        return cls(events, event_id, head_hash)


@dataclass(frozen=True)
class AnchorCheck:
    ok: bool
    reason: str


def take_anchor(log) -> Anchor:
    """Anchor the log as it is now; count and head come from one snapshot.

    The chain is verified first (``ChainError`` if broken): an anchor must
    never vouch for history that was already tampered with.
    """
    with log.transaction():
        log.verify()
        events = list(log)
    if not events:
        return Anchor(0, "", GENESIS_HASH)
    return Anchor(len(events), events[-1].event_id, events[-1].hash)


def check_anchor(log, anchor: Anchor) -> AnchorCheck:
    """Does the log still contain the history the anchor recorded?"""
    with log.transaction():
        try:
            log.verify()
        except ChainError as exc:
            return AnchorCheck(False, f"chain broken: {exc}")
        events = list(log)
    if len(events) < anchor.events:
        return AnchorCheck(False, f"log shorter than anchor: "
                                  f"{anchor.events - len(events)} events missing")
    if anchor.events:
        event = events[anchor.events - 1]
        if (event.event_id, event.hash) != (anchor.event_id, anchor.head_hash):
            return AnchorCheck(False, "history replaced at or before the anchor")
    return AnchorCheck(True, f"log holds the anchored history "
                             f"({len(events) - anchor.events} events appended since)")
