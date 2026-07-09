"""The agent loop (§7) — the only place memory, Brain, gate, approver, and
executor meet. Each stays ignorant of the others; the loop wires them and is
the sole writer of the task's events, so the gate (pure) and approver (a
decision, not a logger) stay clean.

Sequence per §7, with every arrow an event:

    task.initiated
      └► retrieve                → agent.retrieval.performed
      └► Brain proposes          → agent.action.proposed
      └► Gate evaluates          → gate.verdict.issued
           ├─ ALLOW    ─► execute            → action.executed | action.failed
           ├─ DENY     ─► block              → action.blocked
           └─ ESCALATE ─► approval.requested
                          └► approver        → approval.decided
                               ├─ approve ─► execute → action.executed|failed
                               └─ reject  ─► block   → action.blocked
      └► loop or finish          → task.completed | task.aborted

``causation_id`` is threaded through every hop, so the audit view can walk the
full why-chain from any outcome back to the task and grant that authorized it.

The ``Executor`` here is a protocol only — the sandboxed fs executor is build
step 6. The loop counts authorized executions and feeds ``actions_used`` back
to the gate, which enforces the grant's ``max_actions`` circuit breaker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..approver import ApprovalSurface, Approver
from ..events import Actor, EventLog
from ..events import taxonomy as T
from ..gate import Gate, GateDecision, Policy, Verdict
from ..memory import MemoryCore
from ..scope import ScopeGrant, ToolGrant
from .brain import Brain
from .proposal import Proposal


class ToolExecutionError(Exception):
    """Raised by an executor when a tool run fails. Execution failure is not a
    policy block — the loop records it as ``action.failed``, never
    ``action.blocked``."""


@runtime_checkable
class Executor(Protocol):
    """What the loop needs from build step 6. The real implementation re-checks
    the ToolGrant's constraints (defense in depth); the loop hands them over so
    it can."""

    def execute(self, proposal: Proposal, tool_grant: ToolGrant) -> dict: ...


@dataclass(frozen=True)
class StepOutcome:
    proposal: Proposal
    decision: GateDecision
    outcome_event_type: str      # action.executed | action.failed | action.blocked


@dataclass
class TaskResult:
    task_id: str
    completed: bool              # True → task.completed, False → task.aborted
    reason: str
    steps: list[StepOutcome] = field(default_factory=list)


class AgentLoop:
    def __init__(
        self,
        *,
        memory: MemoryCore,
        gate: Gate,
        policy: Policy,
        approver: Approver,
        executor: Executor,
        agent_id: str = "tenet-agent",
        max_steps: int = 20,
    ) -> None:
        self._memory = memory
        self._log: EventLog = memory.log
        self._gate = gate
        self._policy = policy
        self._approver = approver
        self._executor = executor
        self._agent = Actor(kind="agent", id=agent_id)
        self._max_steps = max_steps

    # -- event helpers -------------------------------------------------------
    def _emit(self, namespace: str, actor: Actor, event_type: str, payload: dict,
              correlation_id: str, causation_id: str | None):
        return self._log.append(
            namespace=namespace, actor=actor, event_type=event_type,
            payload=payload, correlation_id=correlation_id, causation_id=causation_id,
        )

    # -- the loop -------------------------------------------------------------
    def run_task(
        self,
        *,
        task: str,
        grant: ScopeGrant,
        brain: Brain,
        namespace: str,
    ) -> TaskResult:
        corr = grant.task_id
        result = TaskResult(task_id=corr, completed=False, reason="")

        initiated = self._emit(
            namespace, Actor(kind="user", id=grant.principal), T.TASK_INITIATED,
            {"description": task, "grant": grant.to_payload()}, corr, None,
        )

        # retrieve once, scope-bound, as DATA (§7). The query is the task text;
        # richer query planning is a Brain upgrade, not a loop change.
        context = self._memory.retrieve(task, grant)
        retrieval = self._emit(
            namespace, self._agent, T.AGENT_RETRIEVAL_PERFORMED,
            {"query": task, "scope": sorted(grant.namespaces),
             "context_ids": [r.record.context_id for r in context],
             "scores": [round(r.score, 6) for r in context]},
            corr, initiated.event_id,
        )

        actions_used = 0
        cause = retrieval.event_id
        for _ in range(self._max_steps):
            proposal = brain.propose(task, context)
            if proposal is None:
                result.completed, result.reason = True, "brain signalled done"
                self._emit(namespace, self._agent, T.TASK_COMPLETED,
                           {"summary": result.reason, "actions_used": actions_used},
                           corr, cause)
                return result

            proposed = self._emit(
                namespace, self._agent, T.AGENT_ACTION_PROPOSED,
                {"tool": proposal.tool, "args": proposal.args,
                 "justification": proposal.justification,
                 "cited_context_ids": list(proposal.cited_context_ids)},
                corr, cause,
            )

            decision = self._gate.evaluate(
                proposal, grant, self._policy, actions_used=actions_used,
            )
            verdict = self._emit(
                namespace, Actor(kind="gate", id="gate"), T.GATE_VERDICT_ISSUED,
                {"verdict": decision.verdict.value,
                 "policy_version": self._policy.version,
                 "matched_rule_ids": list(decision.matched_rule_ids),
                 "rationale": decision.rationale},
                corr, proposed.event_id,
            )

            if decision.verdict is Verdict.ESCALATE:
                # The surface is built from the proposal + gate decision ONLY —
                # never memory content (§7).
                surface = ApprovalSurface(
                    tool=proposal.tool, args=proposal.args,
                    justification=proposal.justification,
                    gate_rationale=decision.rationale,
                    matched_rule_ids=decision.matched_rule_ids,
                )
                requested = self._emit(
                    namespace, Actor(kind="system", id="loop"), T.APPROVAL_REQUESTED,
                    {"proposal_ref": proposed.event_id, "surface": surface.render()},
                    corr, verdict.event_id,
                )
                approval = self._approver.decide(surface)
                decided = self._emit(
                    namespace, Actor(kind="approver", id=approval.approver_id),
                    T.APPROVAL_DECIDED,
                    {"decision": "approve" if approval.approved else "reject",
                     "approver_id": approval.approver_id, "note": approval.note},
                    corr, requested.event_id,
                )
                if approval.approved:
                    cause, outcome = self._execute(
                        namespace, proposal, grant, corr, decided.event_id)
                    actions_used += 1
                else:
                    blocked = self._emit(
                        namespace, Actor(kind="system", id="loop"), T.ACTION_BLOCKED,
                        {"proposal_ref": proposed.event_id,
                         "verdict_ref": verdict.event_id},
                        corr, decided.event_id,
                    )
                    cause, outcome = blocked.event_id, T.ACTION_BLOCKED

            elif decision.verdict is Verdict.ALLOW:
                cause, outcome = self._execute(
                    namespace, proposal, grant, corr, verdict.event_id)
                actions_used += 1

            else:  # DENY
                blocked = self._emit(
                    namespace, Actor(kind="system", id="loop"), T.ACTION_BLOCKED,
                    {"proposal_ref": proposed.event_id, "verdict_ref": verdict.event_id},
                    corr, verdict.event_id,
                )
                cause, outcome = blocked.event_id, T.ACTION_BLOCKED

            result.steps.append(StepOutcome(proposal, decision, outcome))

        result.completed, result.reason = False, f"step limit ({self._max_steps}) reached"
        self._emit(namespace, Actor(kind="system", id="loop"), T.TASK_ABORTED,
                   {"reason": result.reason}, corr, cause)
        return result

    def _execute(self, namespace: str, proposal: Proposal, grant: ScopeGrant,
                 corr: str, causation_id: str) -> tuple[str, str]:
        """Run one gate-approved intent; event the outcome. Only live verdicts
        drive this — replay never re-executes (§8)."""
        tool_grant = grant.tool_grant(proposal.tool)
        assert tool_grant is not None  # gate guarantees this on ALLOW/ESCALATE-approve
        try:
            observation = self._executor.execute(proposal, tool_grant)
        except ToolExecutionError as exc:
            failed = self._emit(
                namespace, Actor(kind="tool", id=proposal.tool), T.ACTION_FAILED,
                {"error": str(exc)}, corr, causation_id,
            )
            return failed.event_id, T.ACTION_FAILED
        executed = self._emit(
            namespace, Actor(kind="tool", id=proposal.tool), T.ACTION_EXECUTED,
            {"result": observation, "exit_status": "ok"}, corr, causation_id,
        )
        return executed.event_id, T.ACTION_EXECUTED
