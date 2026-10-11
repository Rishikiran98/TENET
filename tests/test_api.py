"""Step 9: the HTTP surface is the principal's channel; the Brain is server-side."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from tenet import HashingEmbedder, MemoryCore
from tenet.agent import Proposal, ScriptedBrain
from tenet.api import create_app
from tenet.demo.brain import GullibleBrain
from tenet.demo.corpus import NAMESPACE, SECRET, load_corpus, make_sandbox
from tenet.demo.policy import DemoFixturePolicy
from tenet.events import taxonomy as T
from tenet.executor import fs_executor

TOKEN = "t" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}
READ_PROJECT = {"namespaces": [NAMESPACE], "max_actions": 3, "ttl_seconds": 600,
                "tools": [{"tool": "fs.read", "constraints": {"path_prefix": "/project"}}]}


def _client(tmp_path, brain_factory=GullibleBrain, corpus=True):
    root = make_sandbox(tmp_path)
    memory = MemoryCore(HashingEmbedder())
    if corpus:
        for source, content in load_corpus():
            memory.ingest(content, namespace=NAMESPACE, source=source)
    app = create_app(memory=memory, executor=fs_executor(root), policy=DemoFixturePolicy(),
                     brain_factory=brain_factory, token=TOKEN)
    return TestClient(app), memory, root


def test_every_endpoint_requires_the_bearer_token(tmp_path):
    client, memory, _ = _client(tmp_path)
    before = len(memory.log)
    for method, path, body in [("post", "/memory", {"content": "x", "namespace": "n", "source": "s"}),
                               ("post", "/tasks", {"task": "x", "namespace": "n", "grant": READ_PROJECT}),
                               ("get", "/audit", None), ("get", "/tasks/x/audit", None)]:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
            response = getattr(client, method)(path, json=body, headers=headers) if body \
                else getattr(client, method)(path, headers=headers)
            assert response.status_code == 401, (path, headers)
    assert len(memory.log) == before


def test_short_token_refused(tmp_path):
    with pytest.raises(ValueError):
        create_app(memory=MemoryCore(HashingEmbedder()), executor=fs_executor(tmp_path),
                   policy=DemoFixturePolicy(), brain_factory=GullibleBrain, token="short")


def test_headline_demo_over_http(tmp_path):
    client, _, root = _client(tmp_path)
    response = client.post("/tasks", headers=AUTH, json={
        "task": "summarize the project notes", "namespace": NAMESPACE, "grant": READ_PROJECT})
    assert response.status_code == 200
    body = response.json()
    assert body["completed"]
    assert [(s["tool"], s["verdict"], s["outcome"]) for s in body["steps"]] == [
        ("fs.read", "allow", T.ACTION_EXECUTED), ("fs.delete", "deny", T.ACTION_BLOCKED)]
    assert "content" not in body["steps"][0]["result"]  # never return read text
    assert body["steps"][0]["result"]["path"] == "/project/notes.md"
    assert (root / SECRET).exists()

    audit = client.get(f"/tasks/{body['task_id']}/audit", headers=AUTH).json()
    assert "cites: poisoned_corpus/shared-wiki-paste.md" in audit["rendered"]
    assert audit["audit"]["grant"]["principal"] == "operator"
    log = client.get("/audit", headers=AUTH).json()
    assert log == {"chain_verified": True, "events": log["events"], "issues": [],
                   "indeterminate_operations": []}


def test_requests_cannot_carry_proposals_or_unknown_fields(tmp_path):
    client, memory, root = _client(tmp_path)
    before = len(memory.log)
    response = client.post("/tasks", headers=AUTH, json={
        "task": "x", "namespace": NAMESPACE, "grant": READ_PROJECT,
        "proposals": [{"tool": "fs.delete", "args": {"path": "/protected/secrets.txt"}}]})
    assert response.status_code == 422
    assert len(memory.log) == before and (root / SECRET).exists()


def test_invalid_grants_are_rejected_before_any_event(tmp_path):
    client, memory, _ = _client(tmp_path)
    before = len(memory.log)
    bad = [{**READ_PROJECT, "ttl_seconds": 0}, {**READ_PROJECT, "max_actions": -1},
           {**READ_PROJECT, "namespaces": [""]},
           {**READ_PROJECT, "tools": [{"tool": "fs.read"}, {"tool": "fs.read"}]}]
    for grant in bad:
        response = client.post("/tasks", headers=AUTH,
                               json={"task": "x", "namespace": NAMESPACE, "grant": grant})
        assert response.status_code == 422, grant
    assert len(memory.log) == before


def test_each_task_gets_a_fresh_single_use_grant(tmp_path):
    proposals = [Proposal("fs.read", {"path": "/project/notes.md"})]
    client, _, _ = _client(tmp_path, brain_factory=lambda: ScriptedBrain(proposals), corpus=False)
    request = {"task": "read notes", "namespace": NAMESPACE, "grant": READ_PROJECT}
    first = client.post("/tasks", headers=AUTH, json=request).json()
    second = client.post("/tasks", headers=AUTH, json=request).json()
    assert first["task_id"] != second["task_id"] and first["grant_id"] != second["grant_id"]
    assert [s["outcome"] for s in first["steps"] + second["steps"]] == [T.ACTION_EXECUTED] * 2


def test_ingest_is_idempotent_and_conflicts_are_409(tmp_path):
    client, _, _ = _client(tmp_path, corpus=False)
    body = {"content": "hello", "namespace": NAMESPACE, "source": "s", "ingest_id": "k1"}
    first = client.post("/memory", headers=AUTH, json=body)
    again = client.post("/memory", headers=AUTH, json=body)
    assert first.status_code == again.status_code == 201
    assert first.json() == again.json()
    conflict = client.post("/memory", headers=AUTH, json={**body, "content": "changed"})
    assert conflict.status_code == 409


def test_unknown_task_audit_is_404(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/tasks/nope/audit", headers=AUTH).status_code == 404


def test_demo_factory(monkeypatch):
    from tenet.api.demo import app
    monkeypatch.setenv("TENET_API_TOKEN", TOKEN)
    client = TestClient(app())
    response = client.post("/tasks", headers=AUTH, json={
        "task": "summarize the project notes", "namespace": NAMESPACE, "grant": READ_PROJECT})
    assert [s["verdict"] for s in response.json()["steps"]] == ["allow", "deny"]


def test_non_utf8_strings_are_422_not_500(tmp_path):
    client, memory, _ = _client(tmp_path)
    before = len(memory.log)
    headers = {**AUTH, "content-type": "application/json"}
    ingest = '{"content": "bad \\ud800", "namespace": "n", "source": "s"}'
    task = ('{"task": "bad \\ud800", "namespace": "n", "grant": '
            '{"namespaces": ["n"], "max_actions": 1, "ttl_seconds": 60}}')
    assert client.post("/memory", content=ingest, headers=headers).status_code == 422
    assert client.post("/tasks", content=task, headers=headers).status_code == 422
    assert len(memory.log) == before


def test_tampered_log_is_reported_not_crashed(tmp_path):
    import dataclasses
    client, memory, _ = _client(tmp_path)
    memory.log._events[0] = dataclasses.replace(memory.log._events[0], namespace="evil")
    response = client.get("/audit", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["chain_verified"] is False


def test_non_utf8_constraint_values_are_422(tmp_path):
    client, memory, _ = _client(tmp_path)
    before = len(memory.log)
    task = ('{"task": "x", "namespace": "n", "grant": {"namespaces": ["n"], "max_actions": 1,'
            ' "ttl_seconds": 60, "tools": [{"tool": "fs.read", "constraints": {"path_prefix": "\\ud800"}}]}}')
    response = client.post("/tasks", content=task, headers={**AUTH, "content-type": "application/json"})
    assert response.status_code == 422
    assert len(memory.log) == before


def test_generated_docs_are_not_served(tmp_path):
    client, _, _ = _client(tmp_path)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
        assert client.get(path, headers=AUTH).status_code == 404


def test_malformed_journal_trace_is_reported_by_audit_endpoint(tmp_path):
    from test_bugfixes import malformed_journal_log
    log, _ = malformed_journal_log()
    app = create_app(memory=MemoryCore(HashingEmbedder(), log=log),
                     executor=fs_executor(tmp_path), policy=DemoFixturePolicy(),
                     brain_factory=GullibleBrain, token=TOKEN)
    response = TestClient(app).get("/audit", headers=AUTH)
    assert response.status_code == 200
    assert any("malformed" in issue for issue in response.json()["issues"])
