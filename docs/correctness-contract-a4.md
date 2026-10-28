# Amendment A4: external log anchors

October 27, 2026. Package version 0.5.0. This extends A3 and narrows one
limitation that CLAUDE.md §3.1 and the review follow-up state: a hash chain
detects edits to history, but not removal of a valid suffix or replacement of
the whole log.

## Decision

Add an *anchor*: the number of events in the log at some moment, plus the id
and hash of the last of them (`src/tenet/events/anchor.py`). The principal
takes one (`take_anchor`, or `GET /audit/anchor`) and stores it somewhere the
log's host cannot rewrite. Later, `check_anchor` (or
`POST /audit/anchor/verify`) answers one question: does the log still hold the
anchored history?

## Semantics

`take_anchor` verifies the chain first and raises `ChainError` if it is
broken, so an anchor never vouches for history that was already tampered
with. Count and head come from one snapshot taken inside a log transaction.

`check_anchor` returns `ok` only if all of these hold, checked in order:

1. The chain verifies from genesis. Otherwise: `chain broken: …`.
2. The log holds at least as many events as the anchor. Otherwise:
   `log shorter than anchor: N events missing` (suffix truncation).
3. The event at the anchored position has the anchored id and hash.
   Otherwise: `history replaced at or before the anchor` (replacement).

Events appended after the anchor are expected and do not fail the check.

Anchors are parsed strictly because they come from outside the host:

- exactly the three fields;
- a non-negative integer count, not a boolean or float;
- a 64-character lowercase hex hash;
- an empty id exactly when the count is zero, and the genesis hash for an empty log.

Over HTTP, a malformed anchor is 422. A failed check is a result, not an
error, so it returns 200 with `ok: false`.

## What an anchor does not prove

- Anything about events appended after it. Take anchors as often as needed.
- Anything stronger than the place it is kept. An anchor stored on the same
  host as the log protects nothing against that host.
- Freshness: no clock or signature is included. Storing anchors off-host,
  signing them, or publishing them is the operator's choice. Doing it is
  outside this contract.

The threat model is unchanged (§1). Host compromise remains out of scope; an
anchor only lets a later check notice one class of host-level rewriting.
