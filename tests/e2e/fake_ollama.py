"""Tiny stand-in for the Ollama API, used by the end-to-end test (no model download needed)."""

import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 64
MODELS = []


def vector(text):
    v = [0.0] * DIM
    for w in re.findall(r"\w+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
    return v


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for e in events:
            self.wfile.write((json.dumps(e) + "\n").encode())
            self.wfile.flush()

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json({"models": [{"name": m} for m in MODELS]})
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self.path == "/api/pull":
            MODELS.append(body["model"])
            return self._stream([{"status": "pulling", "total": 100, "completed": 60}, {"status": "success"}])
        if self.path == "/api/embed":
            return self._json({"embeddings": [vector(t) for t in body["input"]]})
        if self.path == "/api/chat":
            context = body["messages"][0]["content"]
            if "<documents>" in context:
                answer = (
                    "Here is what your policy says:\n\n"
                    "- **Warranty:** 24 months from the purchase date [1].\n"
                    "- **Returns:** accepted within **30 days**, with the receipt [1][2].\n\n"
                    "Repairs after the warranty period are charged at cost [2]."
                )
            else:
                answer = "Hello! Drop a file into the chat and ask me anything about it."
            words = answer.split(" ")
            tokens = [w + " " for w in words[:-1]] + words[-1:]
            events = [{"message": {"content": t}, "done": False} for t in tokens]
            return self._stream(events + [{"done": True}])
        self._json({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 11434), Handler).serve_forever()
