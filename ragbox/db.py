"""SQLite storage: documents, chunks (+ FTS5 keyword index), chats and messages.

One short-lived connection per operation keeps things thread-safe; WAL mode allows
reads while a document is being indexed.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL,            -- queued | processing | ready | error
    error TEXT,
    chunks INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord INTEGER NOT NULL,
    page INTEGER,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role TEXT NOT NULL,              -- user | assistant
    content TEXT NOT NULL,
    sources TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id, created_at);
"""

FTS_SCHEMA = "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61 remove_diacritics 2')"


def new_id() -> str:
    return uuid.uuid4().hex


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            columns = {r[1] for r in c.execute("PRAGMA table_info(documents)")}
            if "sha256" not in columns:  # databases created by an earlier version
                c.execute("ALTER TABLE documents ADD COLUMN sha256 TEXT")
            try:
                c.execute(FTS_SCHEMA)
                self.has_fts = True
            except sqlite3.OperationalError:  # SQLite built without FTS5: vector search only
                self.has_fts = False
        self.version = 0  # bumped on every chunk change, used to refresh the vector cache

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---------- documents
    def add_document(self, doc_id: str, name: str, ext: str, size: int, sha256: str | None = None) -> dict[str, Any]:
        with self.conn() as c:
            c.execute(
                "INSERT INTO documents(id,name,ext,size,status,sha256,created_at) VALUES(?,?,?,?,?,?,?)",
                (doc_id, name, ext, size, "queued", sha256, time.time()),
            )
        return self.get_document(doc_id)  # type: ignore[return-value]

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def find_by_hash(self, sha256: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM documents WHERE sha256=? AND status!='error'", (sha256,)).fetchone()
        return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM documents ORDER BY created_at DESC")]

    def set_status(self, doc_id: str, status: str, error: str | None = None) -> None:
        with self.conn() as c:
            c.execute("UPDATE documents SET status=?, error=? WHERE id=?", (status, error, doc_id))

    def save_chunks(self, doc_id: str, chunks: list[tuple[int | None, str]], embeddings: list[bytes]) -> None:
        with self.conn() as c:
            self._delete_chunks(c, doc_id)
            for i, ((page, text), emb) in enumerate(zip(chunks, embeddings, strict=True)):
                cur = c.execute(
                    "INSERT INTO chunks(doc_id,ord,page,text,embedding) VALUES(?,?,?,?,?)",
                    (doc_id, i, page, text, emb),
                )
                if self.has_fts:
                    c.execute("INSERT INTO chunks_fts(rowid,text) VALUES(?,?)", (cur.lastrowid, text))
            c.execute("UPDATE documents SET status='ready', error=NULL, chunks=? WHERE id=?", (len(chunks), doc_id))
        self.version += 1

    def _delete_chunks(self, c: sqlite3.Connection, doc_id: str) -> None:
        if self.has_fts:
            c.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE doc_id=?)", (doc_id,))
        c.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def delete_document(self, doc_id: str) -> bool:
        with self.conn() as c:
            self._delete_chunks(c, doc_id)
            deleted = c.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount > 0
        self.version += 1
        return deleted

    def all_embeddings(self) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                "SELECT c.id, c.embedding FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE d.status='ready'"
            ).fetchall()

    def keyword_search(self, match: str, limit: int) -> list[int]:
        if not self.has_fts or not match:
            return []
        with self.conn() as c:
            try:
                rows = c.execute(
                    "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit)
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        return [r[0] for r in rows]

    def get_chunks(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        with self.conn() as c:
            rows = c.execute(
                f"SELECT c.id, c.page, c.text, c.doc_id, d.name FROM chunks c "
                f"JOIN documents d ON d.id=c.doc_id WHERE c.id IN ({marks})",
                ids,
            ).fetchall()
        return {r["id"]: dict(r) for r in rows}

    def count_ready_chunks(self) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id=c.doc_id WHERE d.status='ready'").fetchone()[
                0
            ]

    # ---------- chats
    def create_chat(self, title: str = "New chat") -> dict[str, Any]:
        now = time.time()
        chat = {"id": new_id(), "title": title, "created_at": now, "updated_at": now}
        with self.conn() as c:
            c.execute("INSERT INTO chats(id,title,created_at,updated_at) VALUES(:id,:title,:created_at,:updated_at)", chat)
        return chat

    def list_chats(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM chats ORDER BY updated_at DESC")]

    def get_chat(self, chat_id: str) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return dict(row) if row else None

    def rename_chat(self, chat_id: str, title: str) -> None:
        with self.conn() as c:
            c.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))

    def delete_chat(self, chat_id: str) -> bool:
        with self.conn() as c:
            return c.execute("DELETE FROM chats WHERE id=?", (chat_id,)).rowcount > 0

    def add_message(self, chat_id: str, role: str, content: str, sources: list[dict[str, Any]] | None = None) -> str:
        msg_id = new_id()
        now = time.time()
        with self.conn() as c:
            c.execute(
                "INSERT INTO messages(id,chat_id,role,content,sources,created_at) VALUES(?,?,?,?,?,?)",
                (msg_id, chat_id, role, content, json.dumps(sources or []), now),
            )
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
        return msg_id

    def get_messages(self, chat_id: str) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT id, role, content, sources, created_at FROM messages WHERE chat_id=? ORDER BY created_at",
                (chat_id,),
            ).fetchall()
        return [{**dict(r), "sources": json.loads(r["sources"])} for r in rows]
