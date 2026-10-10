"""Valid and adversarial traces with interleaved proposal-specific authority."""
from datetime import datetime, timezone

from tenet.agent import Proposal
from tenet.approver import ApprovalSurface
from tenet.events import Actor, InMemoryEventLog, taxonomy as T
from tenet.events.audit import audit_trace
from tenet.scope import ScopeGrant, ToolGrant


class Trace:
    def __init__(self):
        self.log = InMemoryEventLog()
        self.grant = ScopeGrant.issue(principal='u', task_id='t', namespaces={'n'},
                                     tools=(ToolGrant('fs.read'),), max_actions=10)
        task = self.emit(T.TASK_INITIATED, {'description':'fixture', 'grant':self.grant.to_payload()})
        self.retrieval = self.emit(T.AGENT_RETRIEVAL_PERFORMED,
                                  {'query':'fixture','scope':['n'],'context_ids':[]}, task)

    def emit(self, kind, payload, cause=None, task='t'):
        return self.log.append(namespace='n', actor=Actor('system','fixture'), event_type=kind,
                               payload=payload, correlation_id=task,
                               causation_id=cause.event_id if cause else None)

    def propose(self, path):
        proposal = Proposal('fs.read', {'path':path})
        return self.emit(T.AGENT_ACTION_PROPOSED,
                         {'tool':proposal.tool,'args':proposal.args,'justification':'',
                          'cited_context_ids':[],'proposal_digest':proposal.digest}, self.retrieval)

    def verdict(self, proposal, cause, value='allow', phase='initial', task='t'):
        return self.emit(T.GATE_VERDICT_ISSUED,
                         {'proposal_ref':proposal.event_id,'phase':phase,'verdict':value,
                          'policy_version':'fixture/v1','matched_rule_ids':[],'rationale':'fixture'}, cause, task)

    def request(self, proposal, verdict):
        surface = ApprovalSurface(proposal.payload['tool'], proposal.payload['args'], '',
                                  verdict.payload['rationale'], (), task_id='t',
                                  grant_id=self.grant.grant_id, policy_version='fixture/v1',
                                  expires_at=self.grant.expires_at, proposal_ref=proposal.event_id)
        return self.emit(T.APPROVAL_REQUESTED, {'proposal_ref':proposal.event_id, 'binding':surface.binding}, verdict)

    def approve(self, proposal, request):
        return self.emit(T.APPROVAL_DECIDED,
                         {'proposal_ref':proposal.event_id,'decision':'approve','approver_id':'u',
                          'binding':request.payload['binding']}, request)

    def dispatch(self, proposal, verdict, task='t'):
        return self.emit(T.ACTION_DISPATCHED,
                         {'operation_id':proposal.event_id,'proposal_digest':proposal.payload['proposal_digest'],
                          'grant_id':self.grant.grant_id,'accepted_at':datetime.now(timezone.utc).isoformat()}, verdict, task)


def test_interleaved_other_proposal_deny_does_not_replace_valid_authority():
    t = Trace(); a = t.propose('a'); initial = t.verdict(a, a)
    final = t.verdict(a, initial, phase='dispatch')
    b = t.propose('b'); t.verdict(b, b, 'deny')
    t.dispatch(a, final)
    report = audit_trace(t.log)
    assert report.issues == ()
    assert report.indeterminate_operations == (a.event_id,)


def test_allow_verdict_cannot_be_borrowed_from_another_proposal():
    t = Trace(); a = t.propose('a'); allow = t.verdict(a, a)
    b = t.propose('b'); t.verdict(b, b, 'deny')
    borrowed = t.verdict(b, allow, phase='dispatch'); t.dispatch(b, borrowed)
    issues = audit_trace(t.log).issues
    assert any('causal proposal mismatch' in issue for issue in issues)
    assert any('no proposal-specific' in issue for issue in issues)


def test_approval_cannot_be_borrowed_from_another_proposal():
    t = Trace(); a = t.propose('a'); initial = t.verdict(a, a, 'escalate'); request = t.request(a, initial)
    b = t.propose('b'); t.verdict(b, b, 'escalate')
    borrowed = t.approve(b, request)
    final = t.verdict(b, borrowed, 'escalate', 'dispatch'); t.dispatch(b, final)
    assert any('approval decision proposal' in issue for issue in audit_trace(t.log).issues)


def test_interleaved_approval_for_original_proposal_remains_valid():
    t = Trace(); a = t.propose('a'); initial = t.verdict(a, a, 'escalate'); request = t.request(a, initial)
    b = t.propose('b'); t.verdict(b, b, 'deny')
    approved = t.approve(a, request); final = t.verdict(a, approved, 'escalate', 'dispatch')
    t.dispatch(a, final)
    assert audit_trace(t.log).issues == ()


def test_matching_but_fabricated_request_and_decision_binding_is_detected():
    t = Trace(); a = t.propose('a'); initial = t.verdict(a, a, 'escalate')
    request = t.emit(T.APPROVAL_REQUESTED, {'proposal_ref':a.event_id,'binding':'a'*64}, initial)
    approved = t.approve(a, request); final = t.verdict(a, approved, 'escalate', 'dispatch'); t.dispatch(a, final)
    assert any('request binding' in issue for issue in audit_trace(t.log).issues)


def test_cross_task_causal_reference_is_rejected():
    t = Trace(); a = t.propose('a'); initial = t.verdict(a, a)
    final = t.verdict(a, initial, phase='dispatch', task='other'); t.dispatch(a, final, task='other')
    assert any('missing task/proposal authority' in issue for issue in audit_trace(t.log).issues)
