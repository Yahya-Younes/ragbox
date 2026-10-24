"""Settings, read once from environment variables (prefix RAGBOX_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"RAGBOX_{name}", default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    ollama_url: str = field(default_factory=lambda: _env("OLLAMA_URL", "http://ollama:11434").rstrip("/"))
    chat_model: str = field(default_factory=lambda: _env("CHAT_MODEL", "gemma3:4b"))
    embed_model: str = field(default_factory=lambda: _env("EMBED_MODEL", "nomic-embed-text"))
    auto_pull: bool = field(default_factory=lambda: _env("AUTO_PULL", "true").lower() in {"1", "true", "yes"})
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", "/data")))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 50))
    chunk_size: int = field(default_factory=lambda: _int("CHUNK_SIZE", 1200))
    chunk_overlap: int = field(default_factory=lambda: _int("CHUNK_OVERLAP", 200))
    top_k: int = field(default_factory=lambda: _int("TOP_K", 6))
    context_window: int = field(default_factory=lambda: _int("CONTEXT_WINDOW", 8192))
    allowed_hosts: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h.strip().lower() for h in _env("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",") if h.strip()
        )
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ragbox.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"


ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}
