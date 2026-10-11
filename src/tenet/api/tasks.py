"""Background task runs and HTTP-delivered approval decisions.

The agent loop is unchanged: it still calls ``approver.decide(surface)`` inline
and logs ``approval.requested`` / ``approval.decided`` with the surface's
binding. ``HttpApprover`` makes that call wait on the task's worker thread until
the principal answers over HTTP, the wait times out, or the server shuts down.
The last two deny, so an unanswered escalation still fails closed.

Everything here is process memory. A restart loses running tasks and pending
approvals; no effect is uncertain then, because dispatch happens only after a
decision. ``GET /tasks/{id}`` falls back to the log for such tasks.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..approver.protocol import ApprovalDecision, ApprovalSurface

RUNNING, AWAITING, COMPLETED, ABORTED, FAILED = (
    "running", "awaiting_approval", "completed", "aborted", "failed")


@dataclass
class PendingApproval:
    surface: ApprovalSurface
    answered: threading.Event = field(default_factory=threading.Event)
    decision: tuple[bool, str, str] | None = None   # (approved, binding, note)


@dataclass
class TaskRecord:
    task_id: str
    grant_id: str
    status: str = RUNNING
    result: object = None            # TaskResult once the loop returns
    error: str | None = None
    operation_id: str | None = None  # set when an effect may lack its receipt
    pending: PendingApproval | None = None


class TaskRegistry:
    """Thread-safe table of the tasks this server process is running."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self._records: dict[str, TaskRecord] = {}
        self.closing = False

    def add(self, record: TaskRecord) -> None:
        with self.lock:
            self._records[record.task_id] = record

    def get(self, task_id: str) -> TaskRecord | None:
        with self.lock:
            return self._records.get(task_id)

    def finish(self, task_id: str, **changes) -> None:
        with self.lock:
            record = self._records[task_id]
            for name, value in changes.items():
                setattr(record, name, value)

    def answer(self, task_id: str, proposal_ref: str, *, approved: bool,
               binding: str, note: str) -> str:
        """Deliver a decision. Returns "ok", "missing" or "binding-mismatch"."""
        with self.lock:
            record = self._records.get(task_id)
            pending = record.pending if record else None
            if pending is None or pending.decision is not None or \
                    pending.surface.proposal_ref != proposal_ref:
                return "missing"
            if binding != pending.surface.binding:
                # Keep it pending: the principal must answer what was shown.
                return "binding-mismatch"
            pending.decision = (approved, binding, note)
            pending.answered.set()
            return "ok"

    def close(self) -> None:
        """Deny every pending approval; refuse new waits (server shutdown)."""
        with self.lock:
            self.closing = True
            for record in self._records.values():
                pending = record.pending
                if pending is not None and pending.decision is None:
                    pending.decision = (False, "", "server shutting down → fail-safe deny")
                    pending.answered.set()


class HttpApprover:
    """An ``Approver`` whose decision arrives through the HTTP API."""

    def __init__(self, registry: TaskRegistry, *, principal: str,
                 approval_timeout: float) -> None:
        self.id = f"{principal}/http"
        self._registry = registry
        self._timeout = approval_timeout

    def _deny(self, note: str) -> ApprovalDecision:
        return ApprovalDecision(False, self.id, note)

    def decide(self, surface: ApprovalSurface) -> ApprovalDecision:
        registry = self._registry
        pending = PendingApproval(surface)
        with registry.lock:
            record = registry._records.get(surface.task_id)
            if record is None or registry.closing:
                return self._deny("no HTTP approval channel → fail-safe deny")
            record.pending, record.status = pending, AWAITING
        wait = self._timeout
        if surface.expires_at:
            remaining = (datetime.fromisoformat(surface.expires_at)
                         - datetime.now(timezone.utc)).total_seconds()
            wait = min(wait, max(0.0, remaining))
        pending.answered.wait(wait)
        with registry.lock:
            # Read the slot under the lock: an answer can race the timeout.
            decision = pending.decision
            if decision is None:
                pending.decision = (False, "", "")  # late answers now get 404
            record.pending, record.status = None, RUNNING
        if decision is None:
            return self._deny("approval timed out → fail-safe deny")
        approved, binding, note = decision
        return ApprovalDecision(approved, self.id, note, binding=binding)
