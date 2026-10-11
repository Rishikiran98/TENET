"""Structural audit using proposal-specific causal references, not task recency.

This checks recorded authority and relationships. It cannot establish external
truth, policy correctness, or historical completeness without an external anchor.
"""
from dataclasses import dataclass
from datetime import datetime
from . import taxonomy as T
from ..values import digest
from ..constraints import check_constraints, ConstraintViolation
from ..approver.protocol import ApprovalSurface


@dataclass(frozen=True)
class TraceAudit:
    issues: tuple[str, ...]
    indeterminate_operations: tuple[str, ...]


def audit_trace(log) -> TraceAudit:
    with log.transaction():
        log.verify()
        events = tuple(log)
    by_id, tasks, proposals, dispatches, outcomes = {}, {}, {}, {}, {}
    verdicts, requests, approvals, evidence = {}, {}, {}, {}
    grants, counts, closed = set(), {}, set()
    task_namespaces, invalid_operations = {}, set()
    issues = []

    def related(ref, event, kind=None):
        other = by_id.get(ref)
        if (other is None or other.correlation_id != event.correlation_id or
                other.namespace != event.namespace or kind is not None and other.event_type != kind):
            return None
        return other

    for e in events:
        p, task, kind = e.payload, e.correlation_id, e.event_type
        if e.schema_version < 2:
            issues.append(f'{e.event_id}: legacy schema cannot certify v2 authority')
        parent = related(e.causation_id, e)
        if parent:
            evidence[e.event_id] = evidence.get(parent.event_id, frozenset())
        if kind == T.TASK_INITIATED:
            g = p['grant']
            valid = (all(isinstance(g.get(k), str) and g[k]
                         for k in ('grant_id', 'task_id', 'expires_at')) and
                     type(g.get('max_actions')) is int and g['max_actions'] >= 0 and
                     isinstance(g.get('tools'), (tuple, list)) and
                     all(hasattr(t, 'get') and isinstance(t.get('tool'), str) for t in g['tools']))
            if not valid:
                issues.append(f'{e.event_id}: malformed task grant')
            else:
                if task in tasks or g['grant_id'] in grants:
                    issues.append(f'{e.event_id}: reused task or grant')
                if g['task_id'] != task:
                    issues.append(f'{e.event_id}: task/grant binding mismatch')
                tasks[task] = g
                task_namespaces[task] = e.namespace
                grants.add(g['grant_id']); counts.setdefault(task, 0)
        elif kind == T.AGENT_RETRIEVAL_PERFORMED:
            evidence[e.event_id] = frozenset(p['context_ids'])
        elif kind == T.AGENT_ACTION_PROPOSED:
            expected = digest({k:p[k] for k in ('tool','args','justification','cited_context_ids')})
            if p.get('proposal_digest') != expected:
                issues.append(f'{e.event_id}: proposal digest mismatch')
            proposals[e.event_id] = e
        elif kind == T.GATE_VERDICT_ISSUED:
            ref = p.get('proposal_ref')
            proposal = related(ref, e, T.AGENT_ACTION_PROPOSED)
            valid, approved = False, False
            if proposal:
                if p.get('phase') == 'initial':
                    valid = parent is proposal
                elif p.get('phase') == 'dispatch' and parent:
                    if parent.event_type == T.GATE_VERDICT_ISSUED:
                        previous = verdicts.get(parent.event_id, {})
                        valid = (previous.get('valid', False) and
                                 parent.payload.get('phase') == 'initial' and
                                 parent.payload.get('proposal_ref') == ref and
                                 parent.payload['verdict'] == 'allow' and
                                 (parent.payload['policy_version'] == p['policy_version'] or p['verdict'] == 'deny'))
                    elif parent.event_type == T.APPROVAL_DECIDED:
                        approval = approvals.get(parent.event_id, {})
                        linked = approval.get('valid', False) and approval.get('proposal_ref') == ref
                        approved = linked and approval.get('policy_version') == p['policy_version']
                        valid = linked and (approved or p['verdict'] == 'deny')
            verdicts[e.event_id] = {'valid':valid, 'approved':approved}
            if not valid:
                issues.append(f'{e.event_id}: verdict causal proposal mismatch')
        elif kind == T.APPROVAL_REQUESTED:
            ref = p['proposal_ref']; proposal = related(ref, e, T.AGENT_ACTION_PROPOSED)
            valid = bool(proposal and parent and parent.event_type == T.GATE_VERDICT_ISSUED and
                         verdicts.get(parent.event_id, {}).get('valid') and
                         parent.payload.get('phase') == 'initial' and
                         parent.payload.get('proposal_ref') == ref and parent.payload['verdict'] == 'escalate')
            g = tasks.get(task)
            if valid and g:
                expected = ApprovalSurface(proposal.payload['tool'], proposal.payload['args'],
                    proposal.payload['justification'], parent.payload['rationale'],
                    parent.payload['matched_rule_ids'], task_id=task, grant_id=g['grant_id'],
                    policy_version=parent.payload['policy_version'], expires_at=g['expires_at'],
                    proposal_ref=ref).binding
                valid = expected == p.get('binding')
            else: valid = False
            requests[e.event_id] = {'valid':valid, 'proposal_ref':ref,
                'policy_version':parent.payload.get('policy_version') if parent else None}
            if not valid: issues.append(f'{e.event_id}: approval request binding or causation mismatch')
        elif kind == T.APPROVAL_DECIDED:
            request = requests.get(e.causation_id, {})
            linked = bool(parent and parent.event_type == T.APPROVAL_REQUESTED and
                          request.get('valid') and request.get('proposal_ref') == p.get('proposal_ref'))
            valid = (linked and p['decision'] == 'approve' and
                     p.get('binding') == parent.payload.get('binding'))
            approvals[e.event_id] = {'valid':valid, 'proposal_ref':p.get('proposal_ref'),
                                    'policy_version':request.get('policy_version')}
            if not linked or p['decision'] == 'approve' and not valid:
                issues.append(f'{e.event_id}: approval decision proposal or binding mismatch')
        elif kind == T.ACTION_BLOCKED:
            ref = p['proposal_ref']
            verdict = related(p['verdict_ref'], e, T.GATE_VERDICT_ISSUED)
            if not verdict or verdict.payload.get('proposal_ref') != ref:
                issues.append(f'{e.event_id}: block verdict proposal mismatch')
            closed.add(ref)
        elif kind == T.ACTION_DISPATCHED:
            op = p['operation_id']
            if op in dispatches or op in closed:
                issues.append(f'{op}: duplicate or previously closed dispatch')
                invalid_operations.add(op)
            dispatches[op] = e
            g, proposal = tasks.get(task), related(op, e, T.AGENT_ACTION_PROPOSED)
            if g is None or proposal is None or task_namespaces.get(task) != e.namespace:
                issues.append(f'{op}: missing task/proposal authority')
            else:
                counts[task] += 1
                if counts[task] > g['max_actions'] or p['grant_id'] != g['grant_id']:
                    issues.append(f'{op}: grant or budget violation')
                tool_grant = next((t for t in g['tools'] if t['tool'] == proposal.payload['tool']), None)
                if tool_grant is None:
                    issues.append(f'{op}: tool outside grant')
                else:
                    try:
                        check_constraints(proposal.payload['tool'], proposal.payload['args'],
                                          tool_grant.get('constraints', {}))
                    except (ConstraintViolation, TypeError) as exc:
                        issues.append(f'{op}: grant constraint violation: {exc}')
                if p['proposal_digest'] != proposal.payload.get('proposal_digest'):
                    issues.append(f'{op}: dispatch digest mismatch')
                if set(proposal.payload['cited_context_ids']) - evidence.get(op, frozenset()):
                    issues.append(f'{op}: citation outside retrieved set')
                try:
                    accepted = datetime.fromisoformat(p['accepted_at'])
                    expiry = datetime.fromisoformat(g['expires_at'])
                    if accepted.tzinfo is None or expiry.tzinfo is None: raise ValueError('naive time')
                    if accepted >= expiry: issues.append(f'{op}: expired dispatch')
                except (KeyError, ValueError, TypeError):
                    issues.append(f'{op}: invalid dispatch time')
            authorization = verdicts.get(e.causation_id, {})
            if not (parent and parent.event_type == T.GATE_VERDICT_ISSUED and
                    parent.payload.get('proposal_ref') == op and parent.payload.get('phase') == 'dispatch' and
                    authorization.get('valid') and
                    (parent.payload['verdict'] == 'allow' or parent.payload['verdict'] == 'escalate' and
                     authorization.get('approved'))):
                issues.append(f'{op}: no proposal-specific recorded authorization')
        elif kind in {T.ACTION_EXECUTED, T.ACTION_FAILED, T.ACTION_INDETERMINATE}:
            op = p.get('operation_id'); dispatch = dispatches.get(op)
            valid = e.schema_version >= 2
            if dispatch is None or related(dispatch.event_id, e) is None:
                issues.append(f'{e.event_id}: outcome without matching dispatch')
                valid = False
            elif (e.causation_id != dispatch.event_id or
                  p.get('proposal_digest') != dispatch.payload['proposal_digest'] or
                  p.get('grant_id') != dispatch.payload['grant_id']):
                issues.append(f'{op}: outcome binding mismatch')
                valid = False
            if op in outcomes:
                issues.append(f'{op}: duplicate outcome')
                invalid_operations.add(op)
            if valid:
                outcomes[op] = kind
            closed.add(op)
        by_id[e.event_id] = e
    unknown = tuple(sorted(op for op in dispatches
                           if op in invalid_operations or op not in outcomes or outcomes[op] == T.ACTION_INDETERMINATE))
    return TraceAudit(tuple(issues), unknown)
