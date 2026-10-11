"""Single-use tasks with bound approvals and dispatch/outcome receipts."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ..approver import ApprovalSurface, Approver
from ..events import Actor, EventLog
from ..events import taxonomy as T
from ..gate import Gate, GateDecision, Policy, Verdict
from ..memory import MemoryCore
from ..scope import ScopeGrant, ToolGrant
from .brain import Brain
from ..contracts import Proposal, ToolExecutionError, Observation
from ..values import digest, freeze
import hashlib


class AuditWriteError(RuntimeError):
    """An effect may have occurred but its audit receipt could not be saved."""
    def __init__(self, operation_id):
        super().__init__(f"missing outcome receipt for operation {operation_id}")
        self.operation_id = operation_id


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
    outcome_event_type: str
    observation: Observation


@dataclass
class TaskResult:
    task_id: str
    completed: bool              # True → task.completed, False → task.aborted
    reason: str
    steps: list[StepOutcome] = field(default_factory=list)


class AgentLoop:
    def __init__(self, *, memory: MemoryCore, gate: Gate, policy: Policy,
                 approver: Approver, executor: Executor,
                 agent_id: str = "tenet-agent", max_steps: int = 20, clock=None):
        if type(max_steps) is not int or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        self._memory, self._log = memory, memory.log
        self._gate, self._policy = gate, policy
        self._approver, self._executor = approver, executor
        self._agent = Actor(kind="agent", id=agent_id)
        self._max_steps = max_steps
        self._clock = memory.clock if clock is None else clock

    def _emit(self, namespace, actor, event_type, payload, correlation_id, causation_id):
        return self._log.append(namespace=namespace, actor=actor, event_type=event_type,
                                payload=payload, correlation_id=correlation_id,
                                causation_id=causation_id)

    def run_task(self, *, task: str, grant: ScopeGrant, brain: Brain,
                 namespace: str) -> TaskResult:
        if not isinstance(task, str):
            raise ValueError("task must be a string")
        with self._log.transaction():
            self._log.verify()
            for event in self._log:
                if event.event_type == T.TASK_INITIATED and (
                    event.correlation_id == grant.task_id or
                    event.payload['grant']['grant_id'] == grant.grant_id
                ):
                    raise ValueError("task or grant already started; resume is unsupported")
            initiated = self._emit(
                namespace, Actor('user', grant.principal), T.TASK_INITIATED,
                {'description': task, 'task_digest': digest(task), 'grant': grant.to_payload()},
                grant.task_id, None)
        try:
            return self._run(task, grant, brain, namespace, initiated)
        except Exception as exc:
            try:
                self._emit(namespace, Actor('system', 'loop'), T.TASK_ABORTED,
                           {'reason': f'{type(exc).__name__}: {exc}'},
                           grant.task_id, initiated.event_id)
            except Exception as audit_error:
                exc.add_note(f"also unable to record task abort: {audit_error}")
            raise

    def _run(self, task, grant, brain, namespace, initiated):
        corr = grant.task_id
        result = TaskResult(corr, False, '')
        context = tuple(self._memory.retrieve(task, grant, now=self._clock()))
        retrieved_ids = {r.record.context_id for r in context}
        retrieval = self._emit(namespace, self._agent, T.AGENT_RETRIEVAL_PERFORMED,
            {'query': task, 'scope': sorted(grant.namespaces),
             'context_ids': [r.record.context_id for r in context],
             'scores': [r.score for r in context],
             'evidence': [{'context_id': r.record.context_id, 'raw_id': r.record.raw_id,
                           'text_hash': digest(r.record.text),
                           'transform_version': r.record.contextualizer_version}
                          for r in context]}, corr, initiated.event_id)
        cause, actions_used = retrieval.event_id, 0
        observations = []
        for _ in range(self._max_steps):
            proposal = brain.propose(task, context, observations=tuple(observations))
            if proposal is None:
                result.completed, result.reason = True, 'brain signalled done'
                self._emit(namespace, self._agent, T.TASK_COMPLETED,
                           {'summary': result.reason, 'actions_used': actions_used}, corr, cause)
                return result
            proposed = self._emit(namespace, self._agent, T.AGENT_ACTION_PROPOSED,
                {'tool': proposal.tool, 'args': proposal.args,
                 'justification': proposal.justification,
                 'cited_context_ids': proposal.cited_context_ids,
                 'proposal_digest': proposal.digest}, corr, cause)
            policy_version = self._policy.version
            if set(proposal.cited_context_ids) - retrieved_ids:
                decision = GateDecision(Verdict.DENY, (), 'citation was not retrieved for this task')
            else:
                decision = self._gate.evaluate(proposal, grant, self._policy,
                                              now=self._clock(), actions_used=actions_used)
            verdict = self._verdict(namespace, corr, proposed.event_id, decision, policy_version, proposed.event_id, 'initial')
            cause = verdict.event_id
            authorized = decision.verdict is Verdict.ALLOW
            surface, approval = None, None
            if decision.verdict is Verdict.ESCALATE:
                surface = ApprovalSurface(
                    proposal.tool, proposal.args, proposal.justification,
                    decision.rationale, decision.matched_rule_ids, task_id=corr,
                    grant_id=grant.grant_id, policy_version=policy_version,
                    expires_at=grant.expires_at, proposal_ref=proposed.event_id)
                requested = self._emit(namespace, Actor('system', 'loop'), T.APPROVAL_REQUESTED,
                    {'proposal_ref': proposed.event_id, 'surface': surface.render(),
                     'binding': surface.binding}, corr, cause)
                approval = self._approver.decide(surface)
                authorized = approval.approved and approval.binding == surface.binding
                decided = self._emit(namespace, Actor('approver', approval.approver_id), T.APPROVAL_DECIDED,
                    {'proposal_ref': proposed.event_id, 'decision': 'approve' if authorized else 'reject',
                     'approver_id': approval.approver_id, 'note': approval.note,
                     'binding': approval.binding, 'binding_valid': approval.binding == surface.binding},
                    corr, requested.event_id)
                cause = decided.event_id
            if authorized:
                dispatch_time = self._clock()
                final = self._gate.evaluate(proposal, grant, self._policy,
                                            now=dispatch_time, actions_used=actions_used)
                authorized = (
                    self._policy.version == policy_version and
                    not grant.is_expired(dispatch_time) and
                    actions_used < grant.max_actions and grant.tool_grant(proposal.tool) is not None and
                    (proposal.tool != 'journal.append' or
                     hasattr(self._executor, 'execute_operation')) and
                    (final.verdict is Verdict.ALLOW or
                     (final.verdict is Verdict.ESCALATE and surface is not None and
                      approval.approved and approval.binding == surface.binding))
                )
                if not authorized and proposal.tool == 'journal.append' and not hasattr(
                        self._executor, 'execute_operation'):
                    final = GateDecision(Verdict.DENY, (), 'executor cannot provide journal dispatch identity')
                elif not authorized:
                    final = GateDecision(Verdict.DENY, (), 'dispatch authorization changed or expired')
                verdict = self._verdict(namespace, corr, cause, final, self._policy.version, proposed.event_id, 'dispatch')
                cause, decision = verdict.event_id, final
            if authorized:
                cause, outcome, data, error = self._execute(namespace, proposal, grant, corr, cause,
                                               proposed.event_id, dispatch_time)
                actions_used += 1
            else:
                blocked = self._emit(namespace, Actor('system', 'loop'), T.ACTION_BLOCKED,
                    {'proposal_ref': proposed.event_id, 'verdict_ref': verdict.event_id}, corr, cause)
                cause, outcome = blocked.event_id, T.ACTION_BLOCKED
                data, error = None, None
            rationale = decision.rationale
            if outcome == T.ACTION_BLOCKED and approval is not None and not (
                approval.approved and approval.binding == surface.binding
            ):
                rationale = 'approval rejected or binding invalid'
            observation = Observation(proposal, cause, outcome, data, error, rationale)
            observations.append(observation)
            result.steps.append(StepOutcome(proposal, decision, outcome, observation))
            if outcome == T.ACTION_INDETERMINATE:
                result.reason = 'tool outcome indeterminate; automatic continuation prohibited'
                break
        else:
            result.reason = f'step limit ({self._max_steps}) reached'
        self._emit(namespace, Actor('system', 'loop'), T.TASK_ABORTED,
                   {'reason': result.reason, 'actions_used': actions_used}, corr, cause)
        return result

    def _verdict(self, namespace, corr, cause, decision, policy_version, proposal_ref, phase):
        return self._emit(namespace, Actor('gate', 'gate'), T.GATE_VERDICT_ISSUED,
            {'proposal_ref': proposal_ref, 'phase': phase,
             'verdict': decision.verdict.value, 'policy_version': policy_version,
             'matched_rule_ids': decision.matched_rule_ids, 'rationale': decision.rationale}, corr, cause)

    def _execute(self, namespace, proposal, grant, corr, cause, operation_id, dispatch_time):
        payload = {'operation_id': operation_id, 'proposal_digest': proposal.digest,
                   'grant_id': grant.grant_id, 'accepted_at': dispatch_time.isoformat()}
        if proposal.tool == 'journal.append':
            payload['executor_id'] = self._executor.store_id
        dispatched = self._log.append_committed(namespace=namespace, actor=Actor('system', 'loop'),
            event_type=T.ACTION_DISPATCHED, payload=payload, correlation_id=corr, causation_id=cause)
        observation, error = None, None
        try:
            if proposal.tool == 'journal.append':
                observation = self._executor.execute_operation(proposal, grant.tool_grant(proposal.tool), dispatched)
            else:
                observation = self._executor.execute(proposal, grant.tool_grant(proposal.tool))
            if not isinstance(observation, Mapping):
                raise TypeError("executor result must be a mapping")
            observation = freeze(observation)
            if proposal.tool == 'journal.append':
                from ..journal import journal_evidence
                if observation != journal_evidence(proposal, dispatched, 'applied'):
                    raise ValueError('invalid journal executor receipt')
            receipt_result = observation
            if proposal.tool == 'fs.read' and isinstance(observation.get('content'), str):
                # Bound even custom executors and never persist file text.
                original = observation['content'].encode('utf-8')
                prefix = original[:4096].decode('utf-8', errors='ignore')
                encoded = prefix.encode('utf-8')
                observation = freeze({'path':proposal.args['path'], 'content':prefix,
                    'content_sha256':hashlib.sha256(encoded).hexdigest(),
                    'bytes_returned':len(encoded),
                    'truncated':bool(observation.get('truncated')) or len(encoded) < len(original)})
                receipt_result = {k:v for k,v in observation.items() if k != 'content'}
            elif proposal.tool == 'fs.read':
                # An arbitrary custom result is not safe to retain as a read receipt.
                receipt_result = {'result_digest':digest(observation)}
            payload = {**payload, 'result': receipt_result, 'exit_status': 'ok'}
            outcome = T.ACTION_EXECUTED
        except ToolExecutionError as exc:
            observation = None
            outcome = T.ACTION_INDETERMINATE if exc.outcome_unknown else T.ACTION_FAILED
            error = str(exc)
            payload = {**payload, 'error': error}
        except Exception as exc:
            observation = None
            outcome = T.ACTION_INDETERMINATE
            error = f'{type(exc).__name__}: {exc}'
            payload = {**payload, 'error': error}
        try:
            receipt = self._emit(namespace, Actor('tool', proposal.tool), outcome,
                                 payload, corr, dispatched.event_id)
        except Exception as exc:
            raise AuditWriteError(operation_id) from exc
        return receipt.event_id, outcome, observation, error
