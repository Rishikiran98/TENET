"""Serve the headline demo over HTTP.

    TENET_API_TOKEN=... uvicorn tenet.api.demo:app --factory

Uses the demo corpus, a temporary sandbox, the deliberately gullible Brain,
and the labeled demo fixture policy — not production policy (D8).
"""

from __future__ import annotations

import os
import tempfile

from ..demo.brain import GullibleBrain
from ..demo.corpus import NAMESPACE, load_corpus, make_sandbox
from ..demo.policy import DemoFixturePolicy
from ..executor import fs_executor
from ..memory import HashingEmbedder, MemoryCore
from .app import create_app


def app():
    token = os.environ.get("TENET_API_TOKEN", "")
    sandbox = make_sandbox(tempfile.mkdtemp(prefix="tenet-sandbox-"))
    memory = MemoryCore(embedder=HashingEmbedder())
    for source, content in load_corpus():
        memory.ingest(content, namespace=NAMESPACE, source=source)
    return create_app(memory=memory, executor=fs_executor(sandbox),
                      policy=DemoFixturePolicy(), brain_factory=GullibleBrain, token=token)
