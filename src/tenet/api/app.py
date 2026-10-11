"""A thin, single-tenant HTTP surface over the governed loop (build step 9).

Every request is the **principal** (§1: the principal is trusted; a malicious
user is out of scope). The bearer token is what makes a caller the principal,
so the server refuses to start without one. Multi-tenant access is out of scope
for v1 (§13).

Intent and authority stay on separate paths (§5). A task request carries the
task text and the ScopeGrant only. The Brain is chosen by whoever deploys the
server (``brain_factory``); HTTP callers can never submit proposals. Every
endpoint maps onto something already evented or derived from the log:

    POST /memory          ingest (memory.raw.appended)
    POST /tasks           run one task under a fresh grant (task.* ... events)
    GET  /tasks/{id}/audit   readable why-chain (projections.audit)
    GET  /audit           hash chain + structural audit of the whole log

There is no approval endpoint: the loop is synchronous, so escalations go to
the server's approver (fail-safe deny by default). Responses never carry read
file content; like the log receipts, they carry metadata only.
"""

from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from .. import __version__
from ..agent import AgentLoop, Brain, Executor
from ..agent.loop import AuditWriteError
from ..approver import Approver, FailSafeDenyApprover
from ..events import new_ulid
from ..events.audit import audit_trace
from ..gate import DefaultGate, Gate, Policy
from ..memory import MemoryCore
from ..memory.core import ProjectionError
from ..projections import audit_view, render_audit
from ..scope import ScopeGrant, ToolGrant
from ..values import thaw

_Name = Field(min_length=1, max_length=256)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestRequest(_Strict):
    content: str = Field(max_length=1_000_000)
    namespace: str = _Name
    source: str = _Name
    media_type: str = Field(default="text/plain", min_length=1, max_length=128)
    ingest_id: str | None = Field(default=None, min_length=1, max_length=128)


class ToolGrantBody(_Strict):
    tool: str = _Name
    constraints: dict = Field(default_factory=dict)


class GrantBody(_Strict):
    namespaces: list[str] = Field(max_length=64)
    tools: list[ToolGrantBody] = Field(default_factory=list, max_length=32)
    max_actions: int = Field(ge=0, le=1000)
    ttl_seconds: int = Field(ge=1, le=86_400)


class TaskRequest(_Strict):
    task: str = Field(min_length=1, max_length=10_000)
    namespace: str = _Name
    grant: GrantBody


def _summarize(result) -> dict:
    """Bounded, content-free view of a step result for the HTTP response."""
    if not result:
        return {}
    return {k: v for k, v in result.items()
            if k in ("path", "bytes_returned", "bytes_written", "deleted",
                     "content_sha256", "truncated")}


def create_app(
    *,
    memory: MemoryCore,
    executor: Executor,
    policy: Policy,
    brain_factory: Callable[[], Brain],
    token: str,
    principal: str = "operator",
    approver: Approver | None = None,
    gate: Gate | None = None,
) -> FastAPI:
    """Build the app. ``policy`` is required: production policy is Sai's (D8)."""
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError("token must be a string of at least 16 characters")
    loop = AgentLoop(memory=memory, gate=gate or DefaultGate(), policy=policy,
                     approver=approver or FailSafeDenyApprover(), executor=executor)
    expected = token.encode("utf-8")

    def require_principal(request: Request) -> str:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode("utf-8"), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        return principal

    app = FastAPI(title="Tenet", version=__version__, dependencies=[Depends(require_principal)])

    @app.post("/memory", status_code=status.HTTP_201_CREATED)
    def ingest(body: IngestRequest) -> dict:
        try:
            raw = memory.ingest(body.content, namespace=body.namespace, source=body.source,
                                media_type=body.media_type, ingest_id=body.ingest_id)
        except ProjectionError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                {"error": "accepted but not projected; retry with the same ingest_id",
                                 "raw_id": exc.raw_id, "ingest_id": exc.ingest_id})
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        return {"raw_id": raw.raw_id, "content_hash": raw.content_hash}

    @app.post("/tasks")
    def run_task(body: TaskRequest) -> dict:
        try:
            grant = ScopeGrant.issue(
                principal=principal, task_id=new_ulid(), namespaces=set(body.grant.namespaces),
                tools=tuple(ToolGrant(t.tool, t.constraints) for t in body.grant.tools),
                max_actions=body.grant.max_actions, ttl_seconds=body.grant.ttl_seconds)
        except ValueError as exc:
            raise HTTPException(422, f"invalid grant: {exc}")
        try:
            result = loop.run_task(task=body.task, grant=grant, brain=brain_factory(),
                                   namespace=body.namespace)
        except AuditWriteError as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                {"error": "effect may have occurred without a receipt",
                                 "task_id": grant.task_id, "operation_id": exc.operation_id})
        except Exception as exc:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                                {"error": f"task aborted: {type(exc).__name__}",
                                 "task_id": grant.task_id})
        return {
            "task_id": result.task_id, "grant_id": grant.grant_id,
            "completed": result.completed, "reason": result.reason,
            "steps": [{
                "tool": s.proposal.tool, "args": thaw(s.proposal.args),
                "cited_context_ids": list(s.proposal.cited_context_ids),
                "verdict": s.decision.verdict.value, "rationale": s.observation.rationale,
                "outcome": s.outcome_event_type, "result": _summarize(s.observation.result),
                "error": s.observation.error,
            } for s in result.steps],
        }

    @app.get("/tasks/{task_id}/audit")
    def task_audit(task_id: str) -> dict:
        try:
            view = audit_view(memory.log, task_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown task")
        return {"audit": asdict(view), "rendered": render_audit(view)}

    @app.get("/audit")
    def log_audit() -> dict:
        trace = audit_trace(memory.log)  # verifies the hash chain first; raises if broken
        return {"chain_verified": True, "events": len(memory.log),
                "issues": list(trace.issues),
                "indeterminate_operations": list(trace.indeterminate_operations)}

    return app
