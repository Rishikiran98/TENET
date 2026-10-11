"""Human-readable audit view: one why-chain per proposal (§9).

A pure fold over the log. It reports what was recorded: what the agent saw,
what it proposed, which memory it cited, what the gate and approver said, and
what happened. Structural validity of those records is ``events.audit``'s job;
this view makes no validity claims of its own.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..events import taxonomy as T
from ..values import thaw

_OUTCOMES = {T.ACTION_EXECUTED, T.ACTION_FAILED, T.ACTION_INDETERMINATE}


@dataclass
class ProposalTrace:
    proposal_id: str
    tool: str
    args: dict
    justification: str
    cited: list[tuple[str, str]]           # (context_id, raw source)
    verdicts: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    outcome: str | None = None
    outcome_detail: dict = field(default_factory=dict)


@dataclass
class TaskAudit:
    task_id: str
    description: str
    grant: dict
    retrieved: list[tuple[str, str]]       # (context_id, raw source)
    proposals: list[ProposalTrace]
    ending: str | None = None
    ending_reason: str = ""


def audit_view(log, task_id: str) -> TaskAudit:
    events = list(log)
    sources = {e.event_id: e.payload.get("source", "?")
               for e in events if e.event_type == T.MEMORY_RAW_APPENDED}
    context_sources: dict[str, str] = {}
    audit: TaskAudit | None = None
    traces: dict[str, ProposalTrace] = {}
    for e in events:
        if e.correlation_id != task_id:
            continue
        p, kind = e.payload, e.event_type
        if kind == T.TASK_INITIATED:
            audit = TaskAudit(task_id, p["description"], thaw(p["grant"]), [], [])
        elif audit is None:
            continue
        elif kind == T.AGENT_RETRIEVAL_PERFORMED:
            for item in p.get("evidence", ()):
                context_sources[item["context_id"]] = sources.get(item["raw_id"], "?")
            audit.retrieved = [(c, context_sources.get(c, "?")) for c in p["context_ids"]]
        elif kind == T.AGENT_ACTION_PROPOSED:
            trace = ProposalTrace(
                e.event_id, p["tool"], thaw(p["args"]), p["justification"],
                [(c, context_sources.get(c, "not retrieved")) for c in p["cited_context_ids"]])
            traces[e.event_id] = trace
            audit.proposals.append(trace)
        elif kind == T.GATE_VERDICT_ISSUED and p.get("proposal_ref") in traces:
            traces[p["proposal_ref"]].verdicts.append(
                {k: thaw(p.get(k)) for k in ("phase", "verdict", "rationale",
                                       "matched_rule_ids", "policy_version")})
        elif kind == T.APPROVAL_DECIDED and p.get("proposal_ref") in traces:
            traces[p["proposal_ref"]].approvals.append(
                {k: p.get(k) for k in ("decision", "approver_id", "note")})
        elif kind == T.ACTION_BLOCKED and p.get("proposal_ref") in traces:
            traces[p["proposal_ref"]].outcome = kind
        elif kind in _OUTCOMES and p.get("operation_id") in traces:
            trace = traces[p["operation_id"]]
            trace.outcome = kind
            trace.outcome_detail = thaw(p.get("result") or {}) or {"error": p.get("error")}
        elif kind in (T.TASK_COMPLETED, T.TASK_ABORTED):
            audit.ending = kind
            audit.ending_reason = p.get("summary") or p.get("reason", "")
    if audit is None:
        raise KeyError(f"no task.initiated event for task {task_id!r}")
    return audit


def _tools(grant: dict) -> str:
    parts = []
    for tg in grant.get("tools", ()):
        constraints = ", ".join(f"{k}={v}" for k, v in sorted(tg.get("constraints", {}).items()))
        parts.append(f"{tg['tool']}({constraints})" if constraints else tg["tool"])
    return ", ".join(parts) or "none"


def render_audit(audit: TaskAudit) -> str:
    g = audit.grant
    lines = [
        f"TASK {audit.task_id}: {audit.description!r}",
        f"  grant {g.get('grant_id')} by {g.get('principal')}: "
        f"namespaces [{', '.join(g.get('namespaces', ()))}], tools [{_tools(g)}], max_actions {g.get('max_actions')}",
        f"  retrieved (as data): {', '.join(src for _, src in audit.retrieved) or 'nothing'}",
    ]
    for n, t in enumerate(audit.proposals, 1):
        lines.append(f"  [{n}] proposed {t.tool} {json.dumps(t.args, sort_keys=True)}")
        lines.append(f"      cites: {', '.join(src for _, src in t.cited) or 'nothing'}")
        for v in t.verdicts:
            lines.append(f"      gate ({v['phase']}): {str(v['verdict']).upper()} - {v['rationale']}"
                         f"  [policy {v['policy_version']}]")
        for a in t.approvals:
            lines.append(f"      approver {a['approver_id']}: {a['decision']} - {a['note']}")
        detail = ""
        if t.outcome_detail:
            detail = " " + json.dumps({k: v for k, v in t.outcome_detail.items()
                                       if k in ("path", "bytes_returned", "deleted", "error")},
                                      sort_keys=True)
        lines.append(f"      outcome: {t.outcome or 'none recorded'}{detail}")
    lines.append(f"  ended: {audit.ending or 'not recorded'} ({audit.ending_reason})")
    return "\n".join(lines)
