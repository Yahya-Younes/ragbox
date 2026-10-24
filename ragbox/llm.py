"""Minimal async client for the Ollama HTTP API (models, embeddings, streaming chat)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import numpy as np


def same_model(a: str, b: str) -> bool:
    """'nomic-embed-text' and 'nomic-embed-text:latest' are the same model."""
    norm = lambda n: n if ":" in n else f"{n}:latest"  # noqa: E731
    return norm(a) == norm(b)


class OllamaError(RuntimeError):
    pass


class Ollama:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=10.0),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def installed_models(self) -> list[str]:
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def pull(self, model: str) -> AsyncIterator[dict[str, Any]]:
        async with self.client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if line.strip():
                    event = json.loads(line)
                    if "error" in event:
                        raise OllamaError(event["error"])
                    yield event

    async def embed(self, model: str, texts: list[str]) -> np.ndarray:
        r = await self.client.post("/api/embed", json={"model": model, "input": texts, "truncate": True})
        if r.status_code >= 400:
            raise OllamaError(_error_text(r))
        vectors = np.asarray(r.json()["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)

    async def chat_stream(self, model: str, messages: list[dict[str, str]], num_ctx: int) -> AsyncIterator[str]:
        payload = {"model": model, "messages": messages, "stream": True, "options": {"num_ctx": num_ctx, "temperature": 0.2}}
        async with self.client.stream("POST", "/api/chat", json=payload) as r:
            if r.status_code >= 400:
                await r.aread()
                raise OllamaError(_error_text(r))
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if "error" in event:
                    raise OllamaError(event["error"])
                token = event.get("message", {}).get("content", "")
                if token:
                    yield token
                if event.get("done"):
                    break


def _error_text(r: httpx.Response) -> str:
    try:
        return r.json().get("error", r.text)
    except ValueError:
        return r.text or f"HTTP {r.status_code}"
