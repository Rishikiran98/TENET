"""The readable audit view folds a why-chain per proposal from the log."""

import pytest

from tenet.demo.corpus import make_sandbox
from tenet.demo.tenet_agent import run_tenet_agent
from tenet.events import taxonomy as T
from tenet.projections import audit_view, render_audit


def test_audit_view_resolves_citations_verdicts_and_outcomes(tmp_path):
    result, memory = run_tenet_agent(make_sandbox(tmp_path))
    audit = audit_view(memory.log, result.task_id)

    assert audit.description == "summarize the project notes"
    assert len(audit.retrieved) == 3
    read, delete = audit.proposals
    assert [src for _, src in read.cited] == ["poisoned_corpus/notes-index.md"]
    assert [v["phase"] for v in read.verdicts] == ["initial", "dispatch"]
    assert read.outcome == T.ACTION_EXECUTED
    assert read.outcome_detail["path"] == "/project/notes.md"
    assert "content" not in read.outcome_detail  # read text is never logged

    assert [src for _, src in delete.cited] == ["poisoned_corpus/shared-wiki-paste.md"]
    assert [v["verdict"] for v in delete.verdicts] == ["deny"]
    assert delete.outcome == T.ACTION_BLOCKED
    assert audit.ending == T.TASK_COMPLETED

    text = render_audit(audit)
    assert "cites: poisoned_corpus/shared-wiki-paste.md" in text
    assert "DENY - tool 'fs.delete' not in grant (default-deny)" in text
    assert "outcome: action.blocked" in text


def test_audit_view_unknown_task(tmp_path):
    _, memory = run_tenet_agent(make_sandbox(tmp_path))
    with pytest.raises(KeyError):
        audit_view(memory.log, "no-such-task")
