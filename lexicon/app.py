"""Web server: JSON API + static frontend.

Visitors upload their own documents; each visitor gets a private in-memory
index (kept by a random session cookie) that is rebuilt whenever their
document set changes.

Run locally:   python app.py
Production:    gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:$PORT
(one worker, because sessions live in that process's memory)
"""
import os
import secrets
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import List, Optional, Tuple

from flask import Flask, abort, jsonify, make_response, request, send_from_directory

from engine import SearchEngine
from engine.document_loader import (UPLOAD_EXTENSIONS, UnsupportedFile,
                                    documents_from_texts, extract_text)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

MAX_FILE_BYTES = 8 * 1024 * 1024          # per file
MAX_REQUEST_BYTES = 32 * 1024 * 1024      # per upload request
MAX_DOCS_PER_SESSION = 100
MAX_CHARS_PER_SESSION = 20_000_000
SESSION_TTL = 2 * 60 * 60                 # seconds of inactivity
MAX_SESSIONS = 200
COOKIE = "lexicon_sid"

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES

CSP = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


class Session:
    __slots__ = ("items", "engine", "touched")

    def __init__(self) -> None:
        self.items: List[Tuple[str, str]] = []       # (file name, text)
        self.engine = SearchEngine([])
        self.touched = time.time()

    def rebuild(self) -> None:
        self.engine = SearchEngine(documents_from_texts(self.items))


class SessionStore:
    def __init__(self) -> None:
        self._data: "OrderedDict[str, Session]" = OrderedDict()
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        for sid in [s for s, v in self._data.items() if now - v.touched > SESSION_TTL]:
            del self._data[sid]
        while len(self._data) > MAX_SESSIONS:
            self._data.popitem(last=False)

    def get(self, sid: Optional[str]) -> Optional[Session]:
        if not sid:
            return None
        with self._lock:
            now = time.time()
            self._evict(now)
            sess = self._data.get(sid)
            if sess:
                sess.touched = now
                self._data.move_to_end(sid)
            return sess

    def create(self) -> Tuple[str, Session]:
        with self._lock:
            now = time.time()
            self._evict(now)
            sid = secrets.token_urlsafe(24)
            sess = Session()
            self._data[sid] = sess
            self._evict(now)
            return sid, sess


sessions = SessionStore()
EMPTY_ENGINE = SearchEngine([])
write_lock = threading.Lock()


def current_engine() -> SearchEngine:
    sess = sessions.get(request.cookies.get(COOKIE))
    return sess.engine if sess else EMPTY_ENGINE


def attach_cookie(response, sid: str):
    secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
    response.set_cookie(COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                        samesite="Lax", secure=secure, path="/")
    return response


@app.after_request
def add_headers(response):
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "public, max-age=300"
    return response


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="That upload is too large. Keep each request under "
                         f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB."), 413


@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/api/search")
def api_search():
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 50))
    except ValueError:
        limit = 20
    return jsonify(current_engine().search(request.args.get("q", ""), limit,
                                           request.args.get("mode", "auto")))


@app.get("/api/spell")
def api_spell():
    return jsonify(current_engine().spellcheck(request.args.get("q", "")))


@app.get("/api/suggest")
def api_suggest():
    return jsonify(suggestions=current_engine().suggest(request.args.get("q", "")))


@app.get("/api/stats")
def api_stats():
    stats = current_engine().stats()
    stats["limits"] = {
        "extensions": list(UPLOAD_EXTENSIONS),
        "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
        "max_docs": MAX_DOCS_PER_SESSION,
    }
    return jsonify(stats)


@app.get("/api/doc/<int:doc_id>")
def api_doc(doc_id: int):
    doc = current_engine().read_document(doc_id, request.args.get("q", ""),
                                         request.args.get("mode", "auto"))
    if doc is None:
        abort(404)
    return jsonify(doc)


@app.post("/api/documents")
def api_upload():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify(error="Choose at least one file to add."), 400

    sid = request.cookies.get(COOKIE)
    added, rejected = [], []
    with write_lock:
        sess = sessions.get(sid)
        if sess is None:
            sid, sess = sessions.create()
        items = list(sess.items)
        chars = sum(len(t) for _, t in items)

        for f in files:
            name = os.path.basename(f.filename.replace("\\", "/"))[:120] or "document"
            ext = Path(name).suffix.lower()
            if ext not in UPLOAD_EXTENSIONS:
                rejected.append({"name": name, "error": "Unsupported type. Use "
                                 + ", ".join(UPLOAD_EXTENSIONS) + "."})
                continue
            data = f.stream.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:
                rejected.append({"name": name, "error":
                                 f"Larger than {MAX_FILE_BYTES // (1024 * 1024)} MB."})
                continue
            try:
                text = extract_text(name, data)
            except UnsupportedFile as exc:
                rejected.append({"name": name, "error": str(exc)})
                continue
            if not text.strip():
                rejected.append({"name": name, "error": "The file is empty."})
                continue

            existing = next((i for i, (n, _) in enumerate(items) if n == name), None)
            if existing is None and len(items) >= MAX_DOCS_PER_SESSION:
                rejected.append({"name": name, "error":
                                 f"Limit of {MAX_DOCS_PER_SESSION} documents reached."})
                continue
            old = len(items[existing][1]) if existing is not None else 0
            if chars - old + len(text) > MAX_CHARS_PER_SESSION:
                rejected.append({"name": name, "error": "Total text limit reached."})
                continue
            chars += len(text) - old
            if existing is None:
                items.append((name, text))
            else:
                items[existing] = (name, text)   # same name: replace
            added.append(name)

        if added:
            sess.items = items
            sess.rebuild()
        stats = sess.engine.stats()

    status = 200 if added else 422
    response = make_response(jsonify(added=added, rejected=rejected, stats=stats), status)
    return attach_cookie(response, sid)


@app.delete("/api/documents/<int:doc_id>")
def api_delete(doc_id: int):
    with write_lock:
        sess = sessions.get(request.cookies.get(COOKIE))
        if sess is None or not 0 <= doc_id < len(sess.items):
            abort(404)
        del sess.items[doc_id]
        sess.rebuild()
        return jsonify(stats=sess.engine.stats())


@app.delete("/api/documents")
def api_clear():
    with write_lock:
        sess = sessions.get(request.cookies.get(COOKIE))
        if sess is not None:
            sess.items = []
            sess.rebuild()
            return jsonify(stats=sess.engine.stats())
    return jsonify(stats=EMPTY_ENGINE.stats())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
