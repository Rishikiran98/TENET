"""Read-only structural audit for A1 traces, independent of AgentLoop control flow.

This checks receipts and recorded authority, not policy correctness or whether
an external effect actually happened. It never retries a dispatched operation.
"""
from dataclasses import dataclass
from datetime import datetime
from . import taxonomy as T
from ..values import digest
from ..constraints import check_constraints, ConstraintViolation


@dataclass(frozen=True)
class TraceAudit:
    issues: tuple[str, ...]
    indeterminate_operations: tuple[str, ...]


def audit_trace(log) -> TraceAudit:
    with log.transaction():
        log.verify()
        events = tuple(log)
    tasks, grants, proposals, dispatches, outcomes = {}, set(), {}, {}, {}
    retrieved, verdicts, approvals, requests, counts = {}, {}, {}, {}, {}
    issues = []
    for e in events:
        p, task, kind = e.payload, e.correlation_id, e.event_type
        if kind == T.TASK_INITIATED:
            g = p['grant']
            valid = (all(isinstance(g.get(k), str) and g[k]
                         for k in ('grant_id', 'task_id', 'expires_at')) and
                     type(g.get('max_actions')) is int and g['max_actions'] >= 0 and
                     isinstance(g.get('tools'), (tuple, list)) and
                     all(hasattr(t, 'get') and isinstance(t.get('tool'), str) for t in g['tools']))
            if not valid:
                issues.append(f'{e.event_id}: malformed task grant')
                continue
            if g['task_id'] != task:
                issues.append(f'{e.event_id}: task/grant binding mismatch')
            if task in tasks or g['grant_id'] in grants:
                issues.append(f'{e.event_id}: reused task or grant')
            tasks[task] = g
            grants.add(g['grant_id'])
            counts[task] = 0
        elif kind == T.AGENT_RETRIEVAL_PERFORMED:
            retrieved[task] = set(p['context_ids'])
        elif kind == T.AGENT_ACTION_PROPOSED:
            expected = digest({k: p[k] for k in ('tool','args','justification','cited_context_ids')})
            if p.get('proposal_digest') != expected:
                issues.append(f'{e.event_id}: proposal digest mismatch')
            proposals[e.event_id] = e
            approvals.pop(task, None)
        elif kind == T.GATE_VERDICT_ISSUED:
            verdicts[task] = p
        elif kind == T.APPROVAL_REQUESTED:
            requests[e.event_id] = p
        elif kind == T.APPROVAL_DECIDED:
            request = requests.get(e.causation_id, {})
            approvals[task] = (p.get('decision') == 'approve' and
                               bool(p.get('binding')) and p.get('binding') == request.get('binding'))
        elif kind == T.ACTION_DISPATCHED:
            op = p['operation_id']
            if op in dispatches:
                issues.append(f'{op}: duplicate dispatch')
            dispatches[op] = e
            g, proposal = tasks.get(task), proposals.get(op)
            if g is None or proposal is None or proposal.correlation_id != task:
                issues.append(f'{op}: missing task/proposal authority')
                continue
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
                except ConstraintViolation as exc:
                    issues.append(f'{op}: grant constraint violation: {exc}')
            if p['proposal_digest'] != proposal.payload.get('proposal_digest'):
                issues.append(f'{op}: dispatch digest mismatch')
            if set(proposal.payload['cited_context_ids']) - retrieved.get(task, set()):
                issues.append(f'{op}: citation outside retrieved set')
            try:
                expired = datetime.fromisoformat(p['accepted_at']) >= datetime.fromisoformat(g['expires_at'])
                if expired: issues.append(f'{op}: expired dispatch')
            except (KeyError, ValueError, TypeError):
                issues.append(f'{op}: invalid dispatch time')
            verdict = verdicts.get(task, {}).get('verdict')
            if verdict != 'allow' and not (verdict == 'escalate' and approvals.get(task)):
                issues.append(f'{op}: no recorded authorization')
        elif kind in {T.ACTION_EXECUTED, T.ACTION_FAILED, T.ACTION_INDETERMINATE}:
            op = p.get('operation_id')
            dispatch = dispatches.get(op)
            if dispatch is None or dispatch.correlation_id != task:
                issues.append(f'{e.event_id}: outcome without matching dispatch')
            elif (e.causation_id != dispatch.event_id or
                  p.get('proposal_digest') != dispatch.payload['proposal_digest'] or
                  p.get('grant_id') != dispatch.payload['grant_id']):
                issues.append(f'{op}: outcome binding mismatch')
            if op in outcomes:
                issues.append(f'{op}: duplicate outcome')
            outcomes[op] = kind
    unknown = tuple(sorted(op for op in dispatches
                           if op not in outcomes or outcomes[op] == T.ACTION_INDETERMINATE))
    return TraceAudit(tuple(issues), unknown)
