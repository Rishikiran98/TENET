"""Trusted operator reconciliation, never a source of new task authority."""
from .contracts import Proposal
from .events import Actor, taxonomy as T
from .events.audit import audit_trace
from .scope import ToolGrant


def reconcile_journal(log, executor, operation_id):
    """Resolve one uncertain journal dispatch without retrying its effect.

    The log transaction serializes recovery workers. The store transaction
    fences the original worker. A crash between stores is safe to inspect again.
    The task stays single-use and its dispatch still consumes budget.
    """
    with log.transaction():
        report = audit_trace(log)
        if report.issues:
            raise ValueError('cannot reconcile an invalid or legacy authority trace')
        events = tuple(log)
        prior = next((e for e in events if e.event_type == T.ACTION_RECONCILED and
                      e.payload['operation_id'] == operation_id), None)
        if prior:
            if prior.payload['executor_id'] != executor.store_id:
                raise ValueError('reconciliation store mismatch')
            return prior
        if operation_id not in report.indeterminate_operations:
            raise ValueError('operation has no unresolved dispatch')
        dispatch = next(e for e in events if e.event_type == T.ACTION_DISPATCHED and
                        e.payload['operation_id'] == operation_id)
        proposed = next(e for e in events if e.event_id == operation_id)
        p = proposed.payload
        proposal = Proposal(p['tool'], p['args'], p['justification'], p['cited_context_ids'])
        if proposal.tool != 'journal.append':
            raise ValueError('only journal.append has authoritative reconciliation evidence')
        initiated = next(e for e in events if e.event_type == T.TASK_INITIATED and
                         e.correlation_id == dispatch.correlation_id)
        tg = next(t for t in initiated.payload['grant']['tools'] if t['tool'] == proposal.tool)
        evidence = executor.reconcile(proposal, ToolGrant(tg['tool'], tg.get('constraints', {})), dispatch)
        return log.append(namespace=dispatch.namespace, actor=Actor('system', 'journal-reconciler'),
            event_type=T.ACTION_RECONCILED, correlation_id=dispatch.correlation_id,
            causation_id=dispatch.event_id,
            payload={'operation_id': operation_id, 'proposal_digest': proposal.digest,
                     'grant_id': dispatch.payload['grant_id'], 'executor_id': executor.store_id,
                     'state': evidence['state'], 'evidence': evidence})
