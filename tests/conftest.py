import hashlib
import json
import re
import time

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from ragbox.config import Settings
from ragbox.main import create_app

DIM = 64


def fake_vector(text: str) -> list[float]:
    """Deterministic bag-of-words embedding: texts sharing words get similar vectors."""
    v = np.zeros(DIM, dtype=np.float32)
    for w in re.findall(r"\w+", text.lower()):
        if w in {"search_document", "search_query"}:
            continue
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v.tolist()


class FakeOllama:
    def __init__(self, installed=("gemma3:4b", "nomic-embed-text:latest")):
        self.installed = list(installed)
        self.chat_requests = []
        self.pulled = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in self.installed]})
        body = json.loads(request.content or b"{}")
        if path == "/api/pull":
            self.pulled.append(body["model"])
            self.installed.append(body["model"])
            lines = [{"status": "pulling", "total": 100, "completed": 50}, {"status": "success"}]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines))
        if path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [fake_vector(t) for t in body["input"]]})
        if path == "/api/chat":
            self.chat_requests.append(body)
            parts = ["The answer ", "is in the ", "document [1]."]
            lines = [{"message": {"content": p}, "done": False} for p in parts] + [{"message": {"content": ""}, "done": True}]
            return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines))
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def fake():
    return FakeOllama()


@pytest.fixture
def make_client(tmp_path):
    def _make(fake, **overrides):
        settings = Settings(data_dir=tmp_path, ollama_url="http://ollama.test", **overrides)
        app = create_app(settings, transport=httpx.MockTransport(fake.handler))
        return TestClient(app, base_url="http://localhost", headers={"X-Ragbox": "1"})

    return _make


def wait_for(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
