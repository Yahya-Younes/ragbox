"""Hybrid retrieval: dense vectors (cosine) + keywords (SQLite FTS5 / BM25), merged with RRF."""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from .db import Database

RRF_K = 60
_WORD = re.compile(r"\w{2,}", re.UNICODE)


def doc_prefix(model: str) -> str:
    return "search_document: " if "nomic" in model else ""


def query_prefix(model: str) -> str:
    return "search_query: " if "nomic" in model else ""


def fts_query(text: str, max_terms: int = 24) -> str:
    """Turn free text into a safe FTS5 OR-query: every word is quoted, so no FTS syntax is injected."""
    words = []
    for w in _WORD.findall(text.lower()):
        if w not in words:
            words.append(w)
    return " OR ".join(f'"{w}"' for w in words[:max_terms])


class VectorIndex:
    """In-memory matrix of normalized chunk embeddings, rebuilt when the database changes."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._version = -1
        self._ids = np.zeros(0, dtype=np.int64)
        self._matrix = np.zeros((0, 0), dtype=np.float32)

    def _refresh(self) -> None:
        if self._version == self.db.version:
            return
        rows = self.db.all_embeddings()
        if rows:
            self._ids = np.fromiter((r["id"] for r in rows), dtype=np.int64, count=len(rows))
            self._matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
        else:
            self._ids = np.zeros(0, dtype=np.int64)
            self._matrix = np.zeros((0, 0), dtype=np.float32)
        self._version = self.db.version

    def search(self, query_vec: np.ndarray, limit: int) -> list[int]:
        self._refresh()
        if self._matrix.size == 0 or self._matrix.shape[1] != query_vec.shape[0]:
            return []
        scores = self._matrix @ query_vec
        top = np.argsort(-scores)[:limit]
        return [int(self._ids[i]) for i in top]


def rrf_merge(rankings: list[list[int]], top_k: int) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    return [cid for cid, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]]


def hybrid_search(db: Database, index: VectorIndex, query_vec: np.ndarray, query_text: str, top_k: int) -> list[dict[str, Any]]:
    pool = max(top_k * 4, 20)
    dense = index.search(query_vec, pool)
    sparse = db.keyword_search(fts_query(query_text), pool)
    ids = rrf_merge([dense, sparse], top_k)
    rows = db.get_chunks(ids)
    return [rows[i] for i in ids if i in rows]
