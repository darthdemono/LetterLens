import base64
import copy
import gzip
import http.client
import io
import json
import os
import runpy
import socket
import struct
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

import agent
import app
from agent import ApiError, GemmaAgent
from config import ROOT
from images import Image

ROOT_DATA = Path(__file__).parent / "data"
PNG_URL = "data:image/png;base64," + base64.b64encode(b"\x89PNG data").decode()


class EchoClient:
    offline = False

    def __init__(self, text="Plain answer"):
        self.text = text
        self.seen = []

    def chat(self, messages, tools=None, on_token=None):
        self.seen.append(copy.deepcopy(messages))
        on_token(self.text)
        return {"role": "assistant", "content": self.text}


class Server:
    def __init__(self, factory=None, host="127.0.0.1", root=ROOT, offline=False):
        factory = factory or (lambda: GemmaAgent(EchoClient()))
        self.http = app.make_app_server(port=0, host=host, factory=factory, root=root,
                                        offline=offline)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02},
                         daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def post(self, path, payload, headers=None):
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self.request("POST", path, json.dumps(payload), merged)

    def chat(self, **payload):
        response, data = self.post("/chat", payload)
        events = [json.loads(line) for line in data.decode().splitlines() if line]
        return response, events

    def close(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def server():
    made = []

    def build(**kwargs):
        made.append(Server(**kwargs))
        return made[-1]

    yield build
    for item in made:
        item.close()


def test_static_pages_are_served(server):
    srv = server()
    for path, (_, ctype) in app.PAGES.items():
        response, body = srv.request("GET", path)
        assert response.status == 200, path
        assert response.getheader("Content-Type") == ctype
        assert response.getheader("X-Content-Type-Options") == "nosniff"
        assert body
    response, body = srv.request("GET", "/")
    assert b"LetterLens" in body
    assert "script-src 'self'" in response.getheader("Content-Security-Policy")
    response, body = srv.request("GET", "/api/countries")
    assert "Hungary" in json.loads(body)
    response, body = srv.request("GET", "/sample/scan.png")
    assert body.startswith(b"\x89PNG")


def test_gzip_etag_and_head(server):
    srv = server()
    response, body = srv.request("GET", "/api/countries", headers={"Accept-Encoding": "gzip"})
    assert response.getheader("Content-Encoding") == "gzip"
    assert "Hungary" in json.loads(gzip.decompress(body))
    etag = response.getheader("ETag")
    response, body = srv.request("GET", "/api/countries", headers={"If-None-Match": etag})
    assert response.status == 304 and body == b""
    response, body = srv.request("HEAD", "/api/countries")
    assert response.status == 200 and body == b""
    response, body = srv.request("GET", "/api/countries")
    assert response.getheader("Content-Encoding") is None and body.startswith(b"{")


def test_status_unknown_routes_and_missing_files(server, tmp_path):
    srv = server()
    response, body = srv.request("GET", "/api/status")
    status = json.loads(body)
    assert status["mode"] == "cloud" and status["version"] == app.VERSION
    assert srv.request("GET", "/nothing")[0].status == 404
    assert srv.request("POST", "/nothing", "{}", {"Content-Type": "application/json"})[0].status == 404
    empty = server(root=str(tmp_path))
    assert empty.request("GET", "/")[0].status == 404


def test_status_payload_modes(monkeypatch):
    monkeypatch.setenv("GEMINI_1_KEY", "a,b")
    assert app.status_payload(False)["keys"] == 2
    offline = app.status_payload(True)
    assert offline["mode"] == "offline" and offline["keys"] == 0
    assert offline["model"] == app.LOCAL_MODEL


def test_zip_contains_only_the_allowlist(tmp_path):
    for name in ["app.py", "README.md", ".env", "notes.py", "ocr_win.ps1"]:
        (tmp_path / name).write_text("x", encoding="utf-8")
    for folder in ["static", "tests", ".claude", "Handoff", "static/__pycache__"]:
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    (tmp_path / "static" / "a.js").write_text("x", encoding="utf-8")
    (tmp_path / "static" / "b.pyc").write_text("x", encoding="utf-8")
    (tmp_path / "static" / "__pycache__" / "c.js").write_text("x", encoding="utf-8")
    (tmp_path / ".claude" / "secret.md").write_text("x", encoding="utf-8")
    (tmp_path / "Handoff" / "n.md").write_text("x", encoding="utf-8")
    names = set(zipfile.ZipFile(io.BytesIO(app.source_zip(str(tmp_path)))).namelist())
    assert names == {"letterlens/app.py", "letterlens/README.md", "letterlens/ocr_win.ps1",
                     "letterlens/static/a.js"}


def test_zip_endpoint_serves_a_download(server):
    response, body = server().request("GET", "/letterlens.zip")
    assert "attachment" in response.getheader("Content-Disposition")
    names = zipfile.ZipFile(io.BytesIO(body)).namelist()
    assert "letterlens/app.py" in names and "letterlens/static/render.js" in names
    assert not [n for n in names if ".env" in n or "__pycache__" in n or ".git/" in n]


def test_host_and_origin_checks(server):
    srv = server()
    assert srv.request("GET", "/", headers={"Host": "evil.example"})[0].status == 403
    assert srv.request("GET", "/", headers={"Host": "localhost:1"})[0].status == 200
    assert srv.request("GET", "/", headers={"Host": "[::1]:80"})[0].status == 200
    assert srv.request("GET", "/", headers={"Host": "[::1]"})[0].status == 200
    host = f"127.0.0.1:{srv.port}"
    assert srv.request("GET", "/", headers={"Origin": "http://evil.example"})[0].status == 403
    assert srv.request("GET", "/", headers={"Origin": f"http://{host}"})[0].status == 200
    response, _ = srv.post("/chat", {"message": "hi"}, {"Origin": "http://evil.example"})
    assert response.status == 403


def test_non_loopback_server_skips_the_host_check(server):
    srv = server(host="0.0.0.0")
    assert srv.request("GET", "/", headers={"Host": "my-laptop.lan"})[0].status == 200
    assert srv.request("GET", "/", headers={"Origin": "http://elsewhere"})[0].status == 403


def test_chat_streams_events_and_reaches_the_agent(server):
    client = EchoClient("Hello there")
    srv = server(factory=lambda: GemmaAgent(client))
    response, events = srv.chat(sid="abc", message="Explain", images=[PNG_URL],
                                files=[{"name": "../../n.md", "content": "# Notice"}],
                                country="Hungary")
    assert response.status == 200
    assert response.getheader("Content-Type") == "application/x-ndjson"
    assert events == [{"t": "Hello there"}, {"done": True}]
    user = client.seen[0][-1]["content"]
    assert "--- Document: n.md ---" in user[0]["text"]
    assert user[1]["image_url"]["url"] == PNG_URL
    assert "njt.hu" in client.seen[0][0]["content"]


def test_sessions_keep_history_and_reset_clears_it(server):
    client = EchoClient()
    srv = server(factory=lambda: GemmaAgent(client))
    srv.chat(sid="s1", message="first")
    srv.chat(sid="s1", message="second")
    assert len(client.seen[1]) == len(client.seen[0]) + 2
    response, body = srv.post("/reset", {"sid": "s1"})
    assert json.loads(body) == {"ok": True}
    assert json.loads(srv.post("/reset", {"sid": "s1"})[1]) == {"ok": False}
    srv.chat(sid="s1", message="third")
    assert len(client.seen[2]) == len(client.seen[0])


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"message": "x", "sid": "bad sid!"}, "Invalid session id"),
        ({"message": "x", "sid": 5}, "Invalid session id"),
        ({"message": 5}, "Message is missing or too long"),
        ({"message": "x" * (app.MAX_MESSAGE_CHARS + 1)}, "too long"),
        ({"message": "x", "country": 5}, "Invalid country"),
        ({"message": "x", "country": "y" * 81}, "Invalid country"),
        ({"message": "x", "images": "nope"}, "must be lists"),
        ({"message": "x", "files": {"a": 1}}, "must be lists"),
        ({"message": "x", "images": [PNG_URL] * 6}, "At most 5"),
        ({"message": "x", "images": ["/etc/passwd"]}, "data URLs"),
        ({"message": "x", "images": ["data:text/html;base64,AAAA"]}, "Unsupported image"),
        ({"message": "x", "files": ["a.txt"]}, "name and text content"),
        ({"message": "x", "files": [{"name": "a.txt", "content": 5}]}, "name and text content"),
        ({"message": "x", "files": [{"name": "a.exe", "content": "x"}]}, "Unsupported file type"),
        ({"message": "x", "files": [{"name": "a.pdf"}]}, "has no data"),
        ({"message": "x", "files": [{"name": "a.pdf", "data": "***"}]}, "not valid base64"),
        ({"message": "x", "files": [{"name": "a.pdf", "data": "aGVsbG8="}]}, "not a PDF"),
        ({"message": "x", "files": [{"name": "a.txt", "content": "y" * 200_001}]}, "too long"),
        ({"message": "  "}, "Nothing to analyse"),
        ({}, "Nothing to analyse"),
    ],
)
def test_chat_validation_errors(server, payload, message):
    response, body = server().post("/chat", payload)
    assert response.status == 400
    assert message in json.loads(body)["error"]


def test_chat_accepts_default_file_name_and_empty_message():
    request = app.validate_chat({"files": [{"content": "hello"}], "message": None})
    assert request["files"] == [{"name": "document.txt", "content": "hello"}]
    assert request["sid"] == "default" and request["country"] is None


def test_validate_rejects_non_objects():
    with pytest.raises(ValueError, match="JSON object"):
        app.validate_chat(["x"])


def test_body_checks(server, monkeypatch):
    srv = server()
    response, body = srv.request("POST", "/chat", "{}", {"Content-Type": "text/plain"})
    assert response.status == 400 and "application/json" in json.loads(body)["error"]
    response, body = srv.request("POST", "/chat", "{not json", {"Content-Type": "application/json"})
    assert "valid JSON" in json.loads(body)["error"]
    response, body = srv.request("POST", "/chat", b"\xff\xfe", {"Content-Type": "application/json"})
    assert "valid JSON" in json.loads(body)["error"]
    monkeypatch.setattr(app, "MAX_BODY", 10)
    response, body = srv.request("POST", "/chat", json.dumps({"message": "long enough"}),
                                 {"Content-Type": "application/json"})
    assert "too large" in json.loads(body)["error"]
    response, body = srv.post("/reset", ["x"])
    assert response.status == 400 and "Invalid session id" in json.loads(body)["error"]
    assert srv.post("/reset", {"sid": "bad sid"})[0].status == 400


def raw_post(port, head, body=b""):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(head.encode() + body)
    data = b""
    while chunk := sock.recv(4096):
        data += chunk
    sock.close()
    return data.decode()


def test_content_length_problems(server):
    port = server().port
    base = f"POST /chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
    assert "Content-Length is required" in raw_post(port, base + "\r\n")
    assert "too large" in raw_post(port, base + "Content-Length: -5\r\n\r\n")


def test_agent_errors_are_sent_to_the_client(server, caplog):
    class Failing:
        def run(self, *args, **kwargs):
            raise self.error

    def failing(error):
        bot = Failing()
        bot.error = error
        return lambda: bot

    for error, expected in [(ApiError(429, "slow down"), "slow down"),
                            (ValueError("bad value"), "bad value"),
                            (RuntimeError("secret path /home/x"), "Unexpected error")]:
        srv = server(factory=failing(error))
        _, events = srv.chat(sid="e", message="x")
        assert expected in events[-1]["error"]
        assert "secret" not in json.dumps(events)
    assert "chat failed" in caplog.text


class SlowAgent:
    def __init__(self, record, wait_then_fail=False):
        self.record = record
        self.wait_then_fail = wait_then_fail

    def run(self, prompt, on_event=None, **kwargs):
        self.record["started"].set()
        if self.wait_then_fail:
            time.sleep(0.5)
            raise ValueError("late failure")
        for _ in range(100):
            on_event({"t": "x" * 2000})
            time.sleep(0.01)
        self.record["finished"] = True


def disconnecting_request(port):
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    body = json.dumps({"message": "hi"}).encode()
    sock.sendall((f"POST /chat HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                  f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode()
                 + body)
    sock.recv(10)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


@pytest.mark.parametrize("late_failure", [False, True])
def test_client_disconnect_stops_the_agent(server, late_failure):
    record = {"started": threading.Event(), "finished": False}
    srv = server(factory=lambda: SlowAgent(record, wait_then_fail=late_failure))
    disconnecting_request(srv.port)
    assert record["started"].wait(5)
    time.sleep(1.2)
    assert record["finished"] is False


def test_same_session_requests_do_not_overlap(server):
    state = {"active": 0, "max": 0}

    class Tracking:
        def run(self, prompt, on_event=None, **kwargs):
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
            time.sleep(0.15)
            state["active"] -= 1

    srv = server(factory=Tracking)
    threads = [threading.Thread(target=srv.chat, kwargs={"sid": "same", "message": "x"})
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_session_store_reuse_expiry_and_eviction():
    clock = FakeClock()
    store = app.SessionStore(object, limit=2, ttl=100, clock=clock)
    first = store.acquire("a")
    assert store.acquire("a") is first
    clock.now = 1
    store.acquire("b")
    clock.now = 2
    store.acquire("c")
    assert len(store) == 2 and store.acquire("a") is not first
    clock.now = 500
    store.acquire("d")
    assert len(store) == 1
    assert store.drop("d") is True and store.drop("d") is False


def test_session_store_never_evicts_busy_sessions():
    clock = FakeClock()
    store = app.SessionStore(object, limit=1, ttl=10, clock=clock)
    busy = store.acquire("a")
    busy.lock.acquire()
    clock.now = 100
    store.acquire("b")
    assert len(store) == 2
    clock.now = 101
    assert store.acquire("a") is busy
    busy.lock.release()


def test_file_cache_reloads_changes_and_skips_useless_gzip(tmp_path):
    cache = app.FileCache()
    path = tmp_path / "f.txt"
    path.write_text("short", encoding="utf-8")
    first = cache.get(str(path))
    assert first.compressed is None and cache.get(str(path)) is first
    path.write_text("long text " * 200, encoding="utf-8")
    second = cache.get(str(path))
    assert second is not first and second.compressed and len(second.compressed) < len(second.body)
    path.write_bytes(os.urandom(2000))
    assert cache.get(str(path)).compressed is None
    with pytest.raises(OSError):
        cache.get(str(tmp_path / "missing"))


def test_main_delegates_non_serve_commands(monkeypatch):
    seen = []
    monkeypatch.setattr(app.agent_module, "main", lambda argv: seen.append(argv) or 7)
    assert app.main(["letter.txt"]) == 7 and seen == [["letter.txt"]]


class StubServer:
    def __init__(self, interrupt):
        self.interrupt = interrupt
        self.stopped = False

    def serve_forever(self):
        if self.interrupt:
            raise KeyboardInterrupt

    def shutdown(self):
        self.stopped = True


@pytest.mark.parametrize("interrupt", [True, False])
def test_main_serve(monkeypatch, capsys, interrupt):
    stub = StubServer(interrupt)
    seen = {}

    def fake_make(port, host, offline):
        seen.update(port=port, host=host, offline=offline)
        return stub

    monkeypatch.setattr(app, "make_app_server", fake_make)
    assert app.main(["serve", "--port", "9001", "--offline", "-v"]) == 0
    assert seen == {"port": 9001, "host": "127.0.0.1", "offline": True}
    assert stub.stopped is interrupt
    assert "offline (local model)" in capsys.readouterr().out
    app.main(["serve"])
    assert "[cloud]" in capsys.readouterr().out


def test_main_uses_argv_by_default(monkeypatch):
    seen = []
    monkeypatch.setattr(sys, "argv", ["app.py", "hello"])
    monkeypatch.setattr(app.agent_module, "main", lambda argv: seen.append(argv) or 0)
    assert app.main() == 0 and seen == [["hello"]]


def test_module_runs_as_script(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["app.py", "serve", "--help"])
    with pytest.raises(SystemExit) as info:
        runpy.run_module("app", run_name="__main__")
    assert info.value.code == 0


def test_default_agent_factory_builds_a_real_agent(server):
    srv = server(factory=None, offline=True)
    response, events = srv.chat(sid="real", message="help")
    assert events[0]["t"] == agent.GENERIC_AI_RESPONSE


def test_image_objects_reach_the_agent(server):
    seen = []

    class Spy:
        def run(self, prompt, images=None, **kwargs):
            seen.extend(images)

    server(factory=Spy).chat(sid="spy", message="x", images=[PNG_URL])
    assert seen == [Image("image/png", b"\x89PNG data")]


def pdf_payload(name):
    raw = (ROOT_DATA / name).read_bytes()
    return base64.b64encode(raw).decode()


def test_pdf_uploads_become_text_or_pictures():
    text = app.validate_chat({"message": "", "files": [
        {"name": "../letter.pdf", "data": pdf_payload("chromium.pdf")}]})
    assert text["files"][0]["name"] == "letter.pdf" and "Tisztelt" in text["files"][0]["content"]
    assert text["images"] == []
    scan = app.validate_chat({"message": "", "files": [
        {"name": "scan.PDF", "data": pdf_payload("scan.pdf")}]})
    assert scan["files"] == [] and scan["images"][0].mime == "image/jpeg"


def test_pdf_too_large_is_rejected(monkeypatch):
    monkeypatch.setattr(app.pdf, "MAX_PDF_BYTES", 10)
    with pytest.raises(ValueError, match="too large"):
        app.validate_chat({"message": "", "files": [
            {"name": "a.pdf", "data": pdf_payload("chromium.pdf")}]})


def test_pdf_reaches_the_agent_over_http(server):
    client = EchoClient("Read it")
    srv = server(factory=lambda: GemmaAgent(client))
    response, events = srv.chat(sid="pdf", message="Explain", files=[
        {"name": "lease.pdf", "data": pdf_payload("chromium.pdf")}])
    assert response.status == 200 and events[-1] == {"done": True}
    assert "--- Document: lease.pdf ---" in client.seen[0][-1]["content"]
