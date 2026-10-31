import argparse
import gzip
import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent as agent_module
from agent import ApiError, build_agent
from config import LOCAL_MODEL, MODEL, ROOT, VERSION, KeyPool
from images import MARKDOWN_EXTS, TEXT_EXTS, parse_data_url
from ocr import LocalOCR

log = logging.getLogger("letterlens.server")

MAX_BODY = int(os.environ.get("LETTERLENS_MAX_BODY", 30 * 1024 * 1024))
MAX_ATTACHMENTS = 5
MAX_FILE_CHARS = 200_000
MAX_MESSAGE_CHARS = 20_000
SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
LOOPBACK = {"127.0.0.1", "localhost", "[::1]", "::1"}

PAGES = {
    "/": ("static/index.html", "text/html; charset=utf-8"),
    "/styles.css": ("static/styles.css", "text/css; charset=utf-8"),
    "/app.js": ("static/app.js", "application/javascript; charset=utf-8"),
    "/render.js": ("static/render.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("static/favicon.svg", "image/svg+xml"),
    "/api/countries": ("rules/country_legal_portals.json", "application/json; charset=utf-8"),
    "/sample/notice.md": ("samples/sample_document.md", "text/markdown; charset=utf-8"),
    "/sample/rent_hu.txt": ("samples/sample_letter_hu.txt", "text/plain; charset=utf-8"),
    "/sample/scan.png": ("samples/sample_letter_scan.png", "image/png"),
}

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)

ZIP_FILES = {
    "README.md", "LICENSE", "NOTICE", "SUBMISSION.md", "pyproject.toml", ".gitignore",
    "app.py", "agent.py", "config.py", "images.py", "ocr.py", "tools.py", "ocr_win.ps1",
}
ZIP_DIRS = {"static", "rules", "samples", "tests", ".github"}
ZIP_SKIP = {"__pycache__", ".pytest_cache", ".ruff_cache"}


class ClientGone(Exception):
    pass


class CachedFile:
    def __init__(self, key, body, compressed, etag):
        self.key = key
        self.body = body
        self.compressed = compressed
        self.etag = etag


class FileCache:
    def __init__(self):
        self._items = {}
        self._lock = threading.Lock()

    def get(self, path):
        stat = os.stat(path)
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            item = self._items.get(path)
            if item and item.key == key:
                return item
        with open(path, "rb") as handle:
            body = handle.read()
        packed = gzip.compress(body, 6) if len(body) > 600 else None
        if packed is not None and len(packed) >= len(body):
            packed = None
        item = CachedFile(key, body, packed, f'"{hashlib.sha1(body).hexdigest()[:16]}"')
        with self._lock:
            self._items[path] = item
        return item


class Session:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.Lock()
        self.used = 0.0


class SessionStore:
    def __init__(self, factory, limit=50, ttl=3600, clock=time.monotonic):
        self.factory = factory
        self.limit = limit
        self.ttl = ttl
        self.clock = clock
        self._items = {}
        self._lock = threading.Lock()

    def acquire(self, sid):
        now = self.clock()
        with self._lock:
            for key in [k for k, s in self._items.items()
                        if now - s.used > self.ttl and not s.lock.locked()]:
                del self._items[key]
            session = self._items.get(sid)
            if session is None:
                if len(self._items) >= self.limit:
                    idle = [k for k, s in self._items.items() if not s.lock.locked()]
                    if idle:
                        del self._items[min(idle, key=lambda k: self._items[k].used)]
                session = self._items[sid] = Session(self.factory())
            session.used = now
            return session

    def drop(self, sid):
        with self._lock:
            return self._items.pop(sid, None) is not None

    def __len__(self):
        return len(self._items)


def validate_chat(body):
    if not isinstance(body, dict):
        raise ValueError("Request must be a JSON object")
    sid = body.get("sid", "default")
    if not isinstance(sid, str) or not SID_RE.match(sid):
        raise ValueError("Invalid session id")
    message = body.get("message") or ""
    if not isinstance(message, str) or len(message) > MAX_MESSAGE_CHARS:
        raise ValueError("Message is missing or too long")
    country = body.get("country")
    if country is not None and (not isinstance(country, str) or len(country) > 80):
        raise ValueError("Invalid country")
    images = body.get("images") or []
    files = body.get("files") or []
    if not isinstance(images, list) or not isinstance(files, list):
        raise ValueError("images and files must be lists")
    if len(images) + len(files) > MAX_ATTACHMENTS:
        raise ValueError(f"At most {MAX_ATTACHMENTS} attachments per message")
    pictures = [parse_data_url(url) for url in images]
    documents = []
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            raise ValueError("Each file needs a name and text content")
        name = os.path.basename(str(item.get("name") or "document.txt"))[:120]
        if os.path.splitext(name)[1].lower() not in TEXT_EXTS | MARKDOWN_EXTS:
            raise ValueError(f"Unsupported file type: {name}. Use an image, .md or .txt file.")
        if len(item["content"]) > MAX_FILE_CHARS:
            raise ValueError(f"{name} is too long")
        documents.append({"name": name, "content": item["content"]})
    if not (message.strip() or pictures or documents):
        raise ValueError("Nothing to analyse")
    return {"sid": sid, "message": message, "images": pictures, "files": documents,
            "country": country}


def source_zip(root=ROOT):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:

        def add(path):
            archive.write(path, os.path.join("letterlens", os.path.relpath(path, root)))

        for name in sorted(os.listdir(root)):
            path = os.path.join(root, name)
            if os.path.isfile(path) and name in ZIP_FILES:
                add(path)
            elif os.path.isdir(path) and name in ZIP_DIRS:
                for folder, dirs, names in os.walk(path):
                    dirs[:] = sorted(d for d in dirs if d not in ZIP_SKIP)
                    for item in sorted(names):
                        if not item.endswith(".pyc"):
                            add(os.path.join(folder, item))
    return buffer.getvalue()


def status_payload(offline):
    return {
        "version": VERSION,
        "mode": "offline" if offline else "cloud",
        "model": LOCAL_MODEL if offline else MODEL,
        "keys": 0 if offline else KeyPool().size(),
        "ocr": LocalOCR.engine(),
    }


def make_app_server(port=8080, host="127.0.0.1", offline=False, factory=None, root=ROOT):
    factory = factory or (lambda: build_agent(offline=offline))
    store = SessionStore(factory)
    cache = FileCache()
    loopback_only = host in LOOPBACK

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "LetterLens"

        def log_message(self, fmt, *args):
            log.debug("%s %s", self.address_string(), fmt % args)

        def _headers(self, code, ctype, length=None, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if length is not None:
                self.send_header("Content-Length", str(length))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _send(self, code, ctype, data, extra=None):
            self._headers(code, ctype, len(data), extra)
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, code, obj):
            self._send(code, "application/json; charset=utf-8", json.dumps(obj).encode("utf-8"),
                       {"Cache-Control": "no-store"})

        def _allowed(self):
            host = self.headers.get("Host", "")
            name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if loopback_only and name not in LOOPBACK:
                self._json(403, {"error": "Forbidden host"})
                return False
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).netloc != host:
                self._json(403, {"error": "Forbidden origin"})
                return False
            return True

        def _file(self, rel, ctype):
            try:
                item = cache.get(os.path.join(root, rel))
            except OSError:
                return self._json(404, {"error": "not found"})
            extra = {"ETag": item.etag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
            if ctype.startswith("text/html"):
                extra["Content-Security-Policy"] = CSP
            if self.headers.get("If-None-Match") == item.etag:
                return self._send(304, ctype, b"", extra)
            body = item.body
            if item.compressed and "gzip" in self.headers.get("Accept-Encoding", ""):
                body = item.compressed
                extra["Content-Encoding"] = "gzip"
            return self._send(200, ctype, body, extra)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._allowed():
                return None
            path = urllib.parse.urlsplit(self.path).path
            if path in PAGES:
                return self._file(*PAGES[path])
            if path == "/api/status":
                return self._json(200, status_payload(offline))
            if path == "/letterlens.zip":
                data = source_zip(root)
                return self._send(200, "application/zip", data, {
                    "Content-Disposition": 'attachment; filename="letterlens.zip"'})
            return self._json(404, {"error": "not found"})

        def _read_body(self):
            if "application/json" not in self.headers.get("Content-Type", ""):
                raise ValueError("Content-Type must be application/json")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise ValueError("Content-Length is required") from None
            if length < 0 or length > MAX_BODY:
                raise ValueError("Request is too large")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ValueError("Body is not valid JSON") from None

        def do_POST(self):
            self.close_connection = True
            if not self._allowed():
                return None
            path = urllib.parse.urlsplit(self.path).path
            if path not in {"/chat", "/reset"}:
                return self._json(404, {"error": "not found"})
            try:
                body = self._read_body()
                if path == "/reset":
                    sid = body.get("sid") if isinstance(body, dict) else None
                    if not isinstance(sid, str) or not SID_RE.match(sid):
                        raise ValueError("Invalid session id")
                    return self._json(200, {"ok": store.drop(sid)})
                request = validate_chat(body)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            return self._stream_chat(request)

        def _stream_chat(self, request):
            self._headers(200, "application/x-ndjson", extra={
                "Cache-Control": "no-store", "Connection": "close", "X-Accel-Buffering": "no"})

            def emit(obj):
                try:
                    self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except OSError:
                    raise ClientGone from None

            session = store.acquire(request["sid"])
            try:
                with session.lock:
                    session.agent.run(
                        request["message"], images=request["images"], files=request["files"],
                        on_event=emit, country=request["country"],
                    )
                emit({"done": True})
            except ClientGone:
                return
            except Exception as exc:
                known = isinstance(exc, (ApiError, ValueError))
                if not known:
                    log.exception("chat failed")
                try:
                    message = "Unexpected error. See the server log."
                    if isinstance(exc, ApiError):
                        message = agent_module.explain(exc)
                    elif known:
                        message = str(exc)
                    emit({"error": message})
                except ClientGone:
                    return

    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["serve"]:
        return agent_module.main(argv)
    parser = argparse.ArgumentParser(prog="letterlens serve")
    parser.add_argument("--host", default=os.environ.get("LETTERLENS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LETTERLENS_PORT", "8080")))
    parser.add_argument("--offline", action="store_true", help="use a local model, no cloud")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv[1:])
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    server = make_app_server(port=args.port, host=args.host, offline=args.offline)
    mode = "offline (local model)" if args.offline else "cloud"
    print(f"LetterLens {VERSION} [{mode}] http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
