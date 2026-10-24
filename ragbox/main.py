"""ragbox web server: REST API + streaming chat + the static web UI."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .chunking import chunk_pages
from .config import ALLOWED_EXTENSIONS, Settings
from .db import Database, new_id
from .llm import Ollama, OllamaError, same_model
from .parsing import ParseError, parse_file
from .prompts import build_messages
from .retrieval import VectorIndex, doc_prefix, hybrid_search, query_prefix

log = logging.getLogger("ragbox")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC_DIR = Path(__file__).parent / "static"
ID_RE = re.compile(r"^[0-9a-f]{32}$")
EMBED_BATCH = 16
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------- request bodies
class NewMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=120)


# ---------------------------------------------------------------- state
class ModelState:
    def __init__(self, name: str) -> None:
        self.name = name
        self.state = "checking"  # checking | downloading | ready | error | unreachable
        self.progress = 0.0
        self.error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "state": self.state, "progress": round(self.progress, 3), "error": self.error}


class App:
    """Everything the routes need, created at startup."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.index = VectorIndex(self.db)
        self.ollama = Ollama(settings.ollama_url, transport=transport)
        self.chat_model = ModelState(settings.chat_model)
        self.embed_model = ModelState(settings.embed_model)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---------- models
    async def ensure_models(self) -> None:
        while True:
            try:
                installed = await self.ollama.installed_models()
                break
            except httpx.HTTPError:
                for m in (self.embed_model, self.chat_model):
                    m.state, m.error = "unreachable", f"Waiting for Ollama at {self.settings.ollama_url}…"
                await asyncio.sleep(3)
        for model in (self.embed_model, self.chat_model):  # embeddings first: indexing can start sooner
            if any(same_model(model.name, name) for name in installed):
                model.state, model.progress, model.error = "ready", 1.0, None
                continue
            if not self.settings.auto_pull:
                model.state, model.error = "error", f"Model not installed. Run: ollama pull {model.name}"
                continue
            await self.pull(model)

    async def pull(self, model: ModelState) -> None:
        model.state, model.progress, model.error = "downloading", 0.0, None
        log.info("Downloading model %s", model.name)
        try:
            async for event in self.ollama.pull(model.name):
                total, done = event.get("total"), event.get("completed")
                if total and done is not None:
                    model.progress = min(done / total, 0.999)
            model.state, model.progress = "ready", 1.0
            log.info("Model %s ready", model.name)
        except (httpx.HTTPError, OllamaError) as exc:
            model.state, model.error = "error", f"Download failed: {exc}"
            log.error("Download of %s failed: %s", model.name, exc)

    # ---------- indexing
    async def worker(self) -> None:
        while True:
            doc_id = await self.queue.get()
            try:
                await self.ingest(doc_id)
            except Exception as exc:  # never let one bad file stop the queue
                log.exception("Indexing %s failed", doc_id)
                self.db.set_status(doc_id, "error", f"Unexpected error: {exc.__class__.__name__}")
            finally:
                self.queue.task_done()

    async def ingest(self, doc_id: str) -> None:
        doc = self.db.get_document(doc_id)
        if not doc:
            return
        while self.embed_model.state != "ready":
            if self.embed_model.state == "error":
                self.db.set_status(doc_id, "error", self.embed_model.error)
                return
            await asyncio.sleep(1)
        self.db.set_status(doc_id, "processing")
        path = self.settings.upload_dir / f"{doc_id}{doc['ext']}"
        try:
            pages = await asyncio.to_thread(parse_file, path, doc["ext"])
        except ParseError as exc:
            self.db.set_status(doc_id, "error", str(exc))
            return
        chunks = chunk_pages(pages, self.settings.chunk_size, self.settings.chunk_overlap)
        prefix = doc_prefix(self.settings.embed_model)
        embeddings: list[bytes] = []
        try:
            for i in range(0, len(chunks), EMBED_BATCH):
                batch = [prefix + text for _, text in chunks[i : i + EMBED_BATCH]]
                vectors = await self.ollama.embed(self.settings.embed_model, batch)
                embeddings += [v.tobytes() for v in vectors]
        except (httpx.HTTPError, OllamaError) as exc:
            self.db.set_status(doc_id, "error", f"Embedding failed: {exc}")
            return
        if self.db.get_document(doc_id) is None:  # deleted while indexing
            return
        await asyncio.to_thread(self.db.save_chunks, doc_id, chunks, embeddings)
        log.info("Indexed %s: %d chunks", doc["name"], len(chunks))


def get_app(request: Request) -> App:
    return request.app.state.rb


# ---------------------------------------------------------------- app factory
def create_app(settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rb = App(settings, transport)
        app.state.rb = rb
        for doc in rb.db.list_documents():  # resume work interrupted by a restart
            if doc["status"] in {"queued", "processing"}:
                rb.queue.put_nowait(doc["id"])
        rb.spawn(rb.ensure_models())
        rb.spawn(rb.worker())
        yield
        for task in list(rb.tasks):
            task.cancel()
        await rb.ollama.aclose()

    app = FastAPI(title="ragbox", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security(request: Request, call_next: Any) -> Any:
        # 1. DNS-rebinding protection: only answer to the host names we expect.
        if "*" not in settings.allowed_hosts:
            host = (request.headers.get("host") or "").lower()
            hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
            if hostname not in settings.allowed_hosts:
                return JSONResponse({"detail": "Host not allowed"}, status_code=400)
        # 2. CSRF protection: state-changing calls need a custom header, which other sites can't send.
        if request.method in UNSAFE_METHODS and request.headers.get("x-ragbox") != "1":
            return JSONResponse({"detail": "Missing X-Ragbox header"}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- status
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        rb = get_app(request)
        return {
            "version": __version__,
            "models": {"chat": rb.chat_model.as_dict(), "embed": rb.embed_model.as_dict()},
            "documents": len(rb.db.list_documents()),
            "max_upload_mb": settings.max_upload_mb,
            "extensions": sorted(ALLOWED_EXTENSIONS),
        }

    @app.post("/api/models/retry")
    async def retry_models(request: Request) -> dict[str, bool]:
        rb = get_app(request)
        if all(m.state in {"ready", "downloading", "checking"} for m in (rb.chat_model, rb.embed_model)):
            return {"started": False}
        rb.spawn(rb.ensure_models())
        return {"started": True}

    # ---------- documents
    @app.get("/api/documents")
    async def list_documents(request: Request) -> list[dict[str, Any]]:
        return get_app(request).db.list_documents()

    @app.post("/api/documents")
    async def upload(request: Request, files: list[UploadFile]) -> list[dict[str, Any]]:
        rb = get_app(request)
        limit = settings.max_upload_mb * 1024 * 1024
        results: list[dict[str, Any]] = []
        for file in files[:50]:
            name = Path(file.filename or "file").name[:200] or "file"
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                results.append({"name": name, "error": f"Unsupported file type ({ext or 'none'})."})
                continue
            doc_id = new_id()
            target = settings.upload_dir / f"{doc_id}{ext}"
            size = 0
            too_big = False
            digest = hashlib.sha256()
            with target.open("wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        too_big = True
                        break
                    digest.update(chunk)
                    out.write(chunk)
            if too_big or size == 0:
                target.unlink(missing_ok=True)
                msg = f"File is larger than {settings.max_upload_mb} MB." if too_big else "File is empty."
                results.append({"name": name, "error": msg})
                continue
            existing = rb.db.find_by_hash(digest.hexdigest())
            if existing:
                target.unlink(missing_ok=True)
                results.append({"name": name, "error": f"Already added as “{existing['name']}”."})
                continue
            results.append(rb.db.add_document(doc_id, name, ext, size, digest.hexdigest()))
            rb.queue.put_nowait(doc_id)
        return results

    @app.delete("/api/documents/{doc_id}")
    async def delete_document(request: Request, doc_id: str) -> dict[str, bool]:
        rb = get_app(request)
        doc = rb.db.get_document(_check_id(doc_id))
        if not doc:
            raise HTTPException(404, "Document not found")
        rb.db.delete_document(doc_id)
        (settings.upload_dir / f"{doc_id}{doc['ext']}").unlink(missing_ok=True)
        return {"deleted": True}

    # ---------- chats
    @app.get("/api/chats")
    async def list_chats(request: Request) -> list[dict[str, Any]]:
        return get_app(request).db.list_chats()

    @app.post("/api/chats")
    async def create_chat(request: Request) -> dict[str, Any]:
        return get_app(request).db.create_chat()

    @app.get("/api/chats/{chat_id}")
    async def get_chat(request: Request, chat_id: str) -> dict[str, Any]:
        db = get_app(request).db
        chat = db.get_chat(_check_id(chat_id))
        if not chat:
            raise HTTPException(404, "Chat not found")
        return {**chat, "messages": db.get_messages(chat_id)}

    @app.patch("/api/chats/{chat_id}")
    async def rename_chat(request: Request, chat_id: str, body: Rename) -> dict[str, bool]:
        db = get_app(request).db
        if not db.get_chat(_check_id(chat_id)):
            raise HTTPException(404, "Chat not found")
        db.rename_chat(chat_id, body.title.strip())
        return {"renamed": True}

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(request: Request, chat_id: str) -> dict[str, bool]:
        if not get_app(request).db.delete_chat(_check_id(chat_id)):
            raise HTTPException(404, "Chat not found")
        return {"deleted": True}

    @app.post("/api/chats/{chat_id}/messages")
    async def send_message(request: Request, chat_id: str, body: NewMessage) -> StreamingResponse:
        rb = get_app(request)
        chat = rb.db.get_chat(_check_id(chat_id))
        if not chat:
            raise HTTPException(404, "Chat not found")
        question = body.content.strip()
        history = rb.db.get_messages(chat_id)
        if not history:
            rb.db.rename_chat(chat_id, _title(question))
        rb.db.add_message(chat_id, "user", question)
        return StreamingResponse(
            _answer(rb, chat_id, question, history),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app


async def _answer(rb: App, chat_id: str, question: str, history: list[dict[str, Any]]) -> AsyncIterator[str]:
    sources: list[dict[str, Any]] = []
    answer = ""
    try:
        if rb.chat_model.state != "ready":
            raise OllamaError("The AI model is still downloading — try again in a moment.")
        if rb.embed_model.state == "ready" and rb.db.count_ready_chunks():
            previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
            search_text = f"{previous}\n{question}" if previous and len(question.split()) < 8 else question
            qvec = (await rb.ollama.embed(rb.settings.embed_model, [query_prefix(rb.settings.embed_model) + search_text]))[0]
            passages = hybrid_search(rb.db, rb.index, qvec, search_text, rb.settings.top_k)
            sources = [
                {"n": i, "doc_id": p["doc_id"], "name": p["name"], "page": p["page"], "text": p["text"][:700]}
                for i, p in enumerate(passages, start=1)
            ]
        else:
            passages = []
        yield _sse("sources", sources)
        messages = build_messages(history, question, passages)
        async for token in rb.ollama.chat_stream(rb.settings.chat_model, messages, rb.settings.context_window):
            answer += token
            yield _sse("token", token)
        yield _sse("done", {})
    except (OllamaError, httpx.HTTPError) as exc:
        log.warning("Answer failed: %s", exc)
        yield _sse("error", {"message": str(exc) or "The AI model isn't reachable."})
    finally:
        if answer:  # keep partial answers if the user stopped the generation
            rb.db.add_message(chat_id, "assistant", answer, sources)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= 60 else title[:57].rstrip() + "…"


def _check_id(value: str) -> str:
    if not ID_RE.match(value):
        raise HTTPException(404, "Not found")
    return value


app = create_app()
