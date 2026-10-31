import base64
import io
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.platform.startswith("win"):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import agent
from agent import (
    Client,
    GeminiClient,
    GemmaAgent,
    KeyPool,
    LocalOCR,
    MarkdownParser,
    MODEL,
    SYSTEM_PROMPT as SYSTEM,
    URL,
    describe,
    country_legal_portal_tool,
)

Agent = GemmaAgent
IMAGE_EXTS = agent.IMAGE_EXTS
TEXT_EXTS = agent.TEXT_EXTS
MARKDOWN_EXTS = agent.MARKDOWN_EXTS


def folder_tools(root):
    root = os.path.realpath(root)
    text_exts = (".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml")

    def _walk():
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in {".git", ".venv", "__pycache__"}]
            for n in sorted(files):
                if n.endswith(text_exts):
                    yield os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/")

    def list_files():
        return "\n".join(sorted(_walk())) or "no files"

    def read_file(path: str):
        p = os.path.realpath(os.path.join(root, path))
        if p != root and not p.startswith(root + os.sep):
            return "error: path outside folder"
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read(20000)

    def search_docs(query: str):
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
        hits = []
        for rel in _walk():
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            score = sum(ln.lower().count(w) for ln in lines for w in words)
            if score:
                best = max(lines, key=lambda l: sum(w in l.lower() for w in words))
                hits.append(f"{rel}: {best.strip()[:180]}")
        return "\n".join(hits[:5]) or "no matches"

    return [
        {
            "name": "list_files",
            "fn": list_files,
            "schema": {
                "type": "function",
                "function": {
                    "name": "list_files",
                    "description": "List readable text files in the rules folder.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        },
        {
            "name": "read_file",
            "fn": read_file,
            "schema": {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a text file from the rules folder by relative path.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
        },
        {
            "name": "search_docs",
            "fn": search_docs,
            "schema": {
                "type": "function",
                "function": {
                    "name": "search_docs",
                    "description": "Keyword search over the rules folder.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                    },
                },
            },
        },
    ]


def build_agent(client=None):
    a = GemmaAgent(client or GeminiClient(), system=SYSTEM)
    a.add_tools([country_legal_portal_tool()])
    rules = os.environ.get("LETTERLENS_RULES")
    if not rules:
        default_rules = os.path.join(ROOT, "rules")
        if os.path.isdir(default_rules):
            rules = default_rules
    if rules and os.path.isdir(rules):
        a.add_tools(folder_tools(rules))
    return a


SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
SKIP_NAMES = (".env", "CLAUDE", "AGENTS", "CODEX", "GEMINI")
ROOT = os.path.dirname(os.path.abspath(__file__))


def source_zip(root=ROOT):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(files):
                if name.startswith(SKIP_NAMES) or name.endswith(".pyc"):
                    continue
                path = os.path.join(dirpath, name)
                z.write(path, os.path.join("letterlens", os.path.relpath(path, root)))
    return buf.getvalue()


STATIC_DIR = os.path.join(ROOT, "static")


def make_app_server(port=8080, host="127.0.0.1"):
    sessions = {}

    def get_agent(sid):
        if sid not in sessions:
            if len(sessions) >= 50:
                sessions.pop(next(iter(sessions)))
            sessions[sid] = build_agent()
        return sessions[sid]

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, content_type, data):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path in {"", "/"}:
                index_path = os.path.join(STATIC_DIR, "index.html")
                if os.path.isfile(index_path):
                    with open(index_path, "rb") as f:
                        self._send(200, "text/html; charset=utf-8", f.read())
                else:
                    self._send(404, "text/plain", b"index.html not found")
            elif parsed_path == "/styles.css":
                css_path = os.path.join(STATIC_DIR, "styles.css")
                if os.path.isfile(css_path):
                    with open(css_path, "rb") as f:
                        self._send(200, "text/css; charset=utf-8", f.read())
                else:
                    self._send(404, "text/plain", b"styles.css not found")
            elif parsed_path == "/app.js":
                js_path = os.path.join(STATIC_DIR, "app.js")
                if os.path.isfile(js_path):
                    with open(js_path, "rb") as f:
                        self._send(200, "application/javascript; charset=utf-8", f.read())
                else:
                    self._send(404, "text/plain", b"app.js not found")
            elif parsed_path == "/api/countries":
                portals_path = os.path.join(ROOT, "rules", "country_legal_portals.json")
                if os.path.isfile(portals_path):
                    with open(portals_path, "rb") as f:
                        self._send(200, "application/json; charset=utf-8", f.read())
                else:
                    self._send(404, "text/plain", b"country portals not found")
            elif parsed_path == "/letterlens.zip":
                data = source_zip()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="letterlens.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed_path == "/sample/notice.md":
                sample_file = os.path.join(ROOT, "samples", "sample_document.md")
                if os.path.isfile(sample_file):
                    with open(sample_file, "rb") as f:
                        data = f.read()
                    self._send(200, "text/markdown; charset=utf-8", data)
                else:
                    self._send(404, "text/plain", b"not found")
            elif parsed_path == "/sample/scan.png":
                sample_img = os.path.join(ROOT, "samples", "sample_letter_scan.png")
                if os.path.isfile(sample_img):
                    with open(sample_img, "rb") as f:
                        b64 = base64.b64encode(f.read()).decode("utf-8")
                    data_url = f"data:image/png;base64,{b64}"
                    self._send(200, "text/plain; charset=utf-8", data_url.encode("utf-8"))
                else:
                    self._send(404, "text/plain", b"not found")
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

        def do_POST(self):
            parsed_path = urllib.parse.urlsplit(self.path).path
            if parsed_path != "/chat":
                return self._send(404, "text/plain; charset=utf-8", b"not found")

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()

            def _emit(obj):
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()

            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                sid = str(body.get("sid", "default"))
                msg = body.get("message", "")
                images = body.get("images") or None
                files = body.get("files") or None
                country = body.get("country") or None

                if images:
                    ocr_results = []
                    for idx, img in enumerate(images):
                        extracted = LocalOCR.extract_text(img)
                        if extracted:
                            ocr_results.append(f"[Document Text Extracted (Image {idx + 1})]:\n\"\"\"\n{extracted}\n\"\"\"")
                    if ocr_results:
                        ocr_block = "\n\n".join(ocr_results)
                        msg = f"{ocr_block}\n\n{msg or 'Explain this document in simple A1-B2 English and cite verified country legal portals with footnotes.'}"

                agent_inst = get_agent(sid)
                if country:
                    agent_inst.set_country_context(country)
                agent_inst.run(msg, images=images, files=files, on_token=lambda t: _emit({"t": t}), country=country)
            except Exception as e:
                _emit({"error": str(e)})

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main(argv):
    if argv[:1] == ["serve"]:
        host = os.environ.get("LETTERLENS_HOST", "127.0.0.1")
        port = int(os.environ.get("LETTERLENS_PORT", "8080"))
        server = make_app_server(port=port, host=host)
        print(f"http://{host}:{port}")
        server.serve_forever()
        return 0
    if not argv:
        print("Usage: python app.py <file-or-text> | serve")
        return 2

    target = argv[0]
    custom_prompt = argv[1] if len(argv) > 1 else None

    describe(target, prompt=custom_prompt, stream=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
