"""A thin, single-tenant HTTP surface over the governed loop (build step 9).

Every request is the **principal** (§1: the principal is trusted; a malicious
user is out of scope). The bearer token is what makes a caller the principal,
so the server refuses to start without one. Multi-tenant access is out of scope
for v1 (§13).

Intent and authority stay on separate paths (§5). A task request carries the
task text and the ScopeGrant only. The Brain is chosen by whoever deploys the
server (``brain_factory``); HTTP callers can never submit proposals. Every
endpoint maps onto something already evented or derived from the log:

    POST /memory                         ingest (memory.raw.appended)
    POST /tasks                          start one task under a fresh grant (202)
    GET  /tasks/{id}                     status, steps, pending approval
    POST /tasks/{id}/approvals/{ref}     answer an escalation (approval.decided)
    GET  /tasks/{id}/audit               readable why-chain (projections.audit)
    GET  /audit                          hash chain + structural audit of the log

Tasks run on worker threads. An ESCALATE verdict waits for the principal to
answer with the binding of the surface they were shown; a wrong binding is
refused and the request stays pending. No answer before the timeout or the
grant's expiry, or a server shutdown, is a deny (see ``api.tasks``). Responses
never carry read file content; like the log receipts, they carry metadata only.
"""

from __future__ import annotations

import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from .. import __version__
from ..agent import AgentLoop, Brain, Executor
from ..agent.loop import AuditWriteError
from ..approver import Approver
from ..events import ChainError, new_ulid
from ..events import taxonomy as T
from ..events.audit import audit_trace
from ..gate import DefaultGate, Gate, Policy
from ..memory import MemoryCore
from ..memory.core import IngestConflict, ProjectionError
from ..projections import audit_view, render_audit
from ..scope import ScopeGrant, ToolGrant
from ..values import thaw
from .tasks import ABORTED, COMPLETED, FAILED, HttpApprover, TaskRecord, TaskRegistry


def _utf8_json(value: dict) -> dict:
    # Pydantic rejects lone surrogates in str fields, but not inside free-form
    # JSON objects; such text cannot be hashed into the log.
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("constraints must be valid UTF-8 text") from exc
    return value


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
    constraints: Annotated[dict, AfterValidator(_utf8_json)] = Field(default_factory=dict)


class GrantBody(_Strict):
    namespaces: list[str] = Field(max_length=64)
    tools: list[ToolGrantBody] = Field(default_factory=list, max_length=32)
    max_actions: int = Field(ge=0, le=1000)
    ttl_seconds: int = Field(ge=1, le=86_400)


class TaskRequest(_Strict):
    task: str = Field(min_length=1, max_length=10_000)
    namespace: str = _Name
    grant: GrantBody


class ApprovalRequest(_Strict):
    decision: Literal["approve", "reject"]
    binding: str = Field(min_length=1, max_length=128)  # echo of the surface shown
    note: str = Field(default="", max_length=1_000)


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
    approval_timeout: float = 300.0,
    max_workers: int = 4,
    max_finished_tasks: int = 1000,
) -> FastAPI:
    """Build the app. ``policy`` is required: production policy is Sai's (D8).

    Without ``approver``, escalations are answered over HTTP by the principal
    (``HttpApprover``); pass one (e.g. ``FailSafeDenyApprover``) to opt out.
    """
    if not isinstance(token, str) or len(token) < 16:
        raise ValueError("token must be a string of at least 16 characters")
    if (not approval_timeout > 0 or type(max_workers) is not int or max_workers < 1 or
            type(max_finished_tasks) is not int or max_finished_tasks < 0):
        raise ValueError("approval_timeout must be positive, max_workers at least 1 "
                         "and max_finished_tasks non-negative")
    registry = TaskRegistry(max_finished=max_finished_tasks)
    approver = approver or HttpApprover(registry, principal=principal,
                                        approval_timeout=approval_timeout)
    loop = AgentLoop(memory=memory, gate=gate or DefaultGate(), policy=policy,
                     approver=approver, executor=executor)
    workers = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tenet-task")
    expected = token.encode("utf-8")

    def require_principal(request: Request) -> str:
        scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode("utf-8"), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        return principal

    # Route dependencies do not cover FastAPI's generated docs, so they are
    # disabled rather than served unauthenticated.
    @asynccontextmanager
    async def lifespan(_app):
        yield
        registry.close()               # pending escalations are denied, not abandoned
        workers.shutdown(wait=True)

    app = FastAPI(title="Tenet", version=__version__, dependencies=[Depends(require_principal)],
                  docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.tasks = registry  # for operators and tests; holds no read content

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default 422 echoes the input, which may itself be invalid
        # UTF-8 (and is untrusted); report only where and why validation failed.
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
                  for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.post("/memory", status_code=status.HTTP_201_CREATED)
    def ingest(body: IngestRequest) -> dict:
        try:
            raw = memory.ingest(body.content, namespace=body.namespace, source=body.source,
                                media_type=body.media_type, ingest_id=body.ingest_id)
        except ProjectionError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                {"error": "accepted but not projected; retry with the same ingest_id",
                                 "raw_id": exc.raw_id, "ingest_id": exc.ingest_id})
        except IngestConflict as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"raw_id": raw.raw_id, "content_hash": raw.content_hash}

    def _steps(result) -> list[dict]:
        return [{
            "tool": s.proposal.tool, "args": thaw(s.proposal.args),
            "cited_context_ids": list(s.proposal.cited_context_ids),
            "verdict": s.decision.verdict.value, "rationale": s.observation.rationale,
            "outcome": s.outcome_event_type, "result": _summarize(s.observation.result),
            "error": s.observation.error,
        } for s in result.steps] if result is not None else []

    def run(record: TaskRecord, body: TaskRequest, grant: ScopeGrant) -> None:
        try:
            result = loop.run_task(task=body.task, grant=grant, brain=brain_factory(),
                                   namespace=body.namespace)
        except AuditWriteError as exc:
            registry.finish(record.task_id, status=FAILED, operation_id=exc.operation_id,
                            error="effect may have occurred without a receipt")
        except Exception as exc:  # the loop has already logged task.aborted
            registry.finish(record.task_id, status=FAILED,
                            error=f"task aborted: {type(exc).__name__}")
        else:
            summary = {"completed": result.completed, "reason": result.reason,
                       "steps": _steps(result)}   # read text is dropped here
            registry.finish(record.task_id, summary=summary,
                            status=COMPLETED if result.completed else ABORTED)

    @app.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
    def start_task(body: TaskRequest) -> dict:
        try:
            grant = ScopeGrant.issue(
                principal=principal, task_id=new_ulid(), namespaces=set(body.grant.namespaces),
                tools=tuple(ToolGrant(t.tool, t.constraints) for t in body.grant.tools),
                max_actions=body.grant.max_actions, ttl_seconds=body.grant.ttl_seconds)
        except ValueError as exc:
            raise HTTPException(422, f"invalid grant: {exc}")
        if registry.closing:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "server shutting down")
        record = TaskRecord(grant.task_id, grant.grant_id)
        registry.add(record)
        workers.submit(run, record, body, grant)
        return {"task_id": grant.task_id, "grant_id": grant.grant_id, "status": record.status}

    def _from_log(task_id: str) -> dict:
        """A task this process is not running (e.g. started before a restart)."""
        started, ending = False, None
        for event in memory.log:
            if event.correlation_id == task_id:
                started = started or event.event_type == T.TASK_INITIATED
                if event.event_type in (T.TASK_COMPLETED, T.TASK_ABORTED):
                    ending = event
        if not started:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown task")
        if ending is None:
            return {"task_id": task_id, "status": "interrupted", "completed": False,
                    "reason": "no terminal event; not running in this server process"}
        done = ending.event_type == T.TASK_COMPLETED
        return {"task_id": task_id, "status": COMPLETED if done else ABORTED, "completed": done,
                "reason": ending.payload.get("summary") or ending.payload.get("reason", "")}

    @app.get("/tasks/{task_id}")
    def task_status(task_id: str) -> dict:
        record = registry.get(task_id)
        if record is None:
            return {**_from_log(task_id), "steps": [], "pending_approval": None}
        with registry.lock:
            pending = record.pending.surface if record.pending else None
            status_, summary = record.status, record.summary or {}
            error, operation_id = record.error, record.operation_id
        return {
            "task_id": task_id, "grant_id": record.grant_id, "status": status_,
            "completed": bool(summary.get("completed")),
            "reason": summary.get("reason"),
            "error": error, "operation_id": operation_id,
            "steps": summary.get("steps", []),
            "pending_approval": None if pending is None else {
                "proposal_ref": pending.proposal_ref, "binding": pending.binding,
                "tool": pending.tool, "args": thaw(pending.args),
                "expires_at": pending.expires_at, "surface": pending.render()},
        }

    @app.post("/tasks/{task_id}/approvals/{proposal_ref}")
    def answer_approval(task_id: str, proposal_ref: str, body: ApprovalRequest) -> dict:
        outcome = registry.answer(task_id, proposal_ref, approved=body.decision == "approve",
                                  binding=body.binding, note=body.note)
        if outcome == "missing":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending approval for this proposal")
        if outcome == "binding-mismatch":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "binding does not match the pending approval; it stays pending")
        return {"task_id": task_id, "proposal_ref": proposal_ref, "decision": body.decision}

    @app.get("/tasks/{task_id}/audit")
    def task_audit(task_id: str) -> dict:
        try:
            view = audit_view(memory.log, task_id)
        except KeyError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown task")
        return {"audit": asdict(view), "rendered": render_audit(view)}

    @app.get("/audit")
    def log_audit() -> dict:
        try:
            trace = audit_trace(memory.log)  # verifies the hash chain first
        except ChainError as exc:
            return JSONResponse(status_code=status.HTTP_409_CONFLICT,
                                content={"chain_verified": False, "error": str(exc)})
        return {"chain_verified": True, "events": len(memory.log),
                "issues": list(trace.issues),
                "indeterminate_operations": list(trace.indeterminate_operations)}

    return app
