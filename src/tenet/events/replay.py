"""Replay — fold the log into a projection.

Every derived view in Tenet (context store, audit view, step history) is the
result of folding events through a reducer. Two rules make this safe:

- **Reducers are pure.** They derive state from events; they never perform side
  effects. Rebuilding a projection is therefore free of consequences.
- **Replay never re-executes actions.** Folding an ``action.executed`` event
  updates a view of what happened; it does NOT run the tool again. Side effects
  happen exactly once, in the executor, driven by a live gate verdict — never on
  replay.

Upgrading a projection = change its reducer and re-fold. Upgrading a
deterministic contextualizer = bump its version and re-fold. That is the
operational payoff of event sourcing.
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

from .envelope import Event
from .log import EventLog

S = TypeVar("S")
Reducer = Callable[[S, Event], S]


def replay(events: Iterable[Event], reducer: Reducer, initial: S) -> S:
    """Fold ``events`` into a projection using ``reducer``, starting from
    ``initial``. Pure: no IO, no side effects."""
    state = initial
    for event in events:
        state = reducer(state, event)
    return state


def rebuild(log: EventLog, reducer: Reducer, initial: S) -> S:
    """Verify the chain, then replay. A projection built on an unverified log is
    a projection you cannot trust, so verification is not optional here."""
    log.verify()
    return replay(iter(log), reducer, initial)
