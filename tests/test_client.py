import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import agent
from agent import ApiError, Client, ThoughtFilter, strip_thoughts
from config import KeyPool


class FakeLLM:
    def __init__(self):
        self.script = []
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                outer.requests.append({"auth": self.headers.get("Authorization"), "body": body,
                                       "path": self.path})
                kind, payload = outer.script.pop(0)
                if kind == "error":
                    code, text = payload
                    data = text.encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                ctype = "text/event-stream" if kind == "sse" else "application/json"
                data = payload.encode()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def llm():
    server = FakeLLM()
    yield server
    server.close()


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(agent.time, "sleep", sleeps.append)
    return sleeps


def sse(*chunks):
    lines = [": keep-alive", "", "event: message"]
    for chunk in chunks:
        lines += ["data: " + (chunk if isinstance(chunk, str) else json.dumps(chunk)), ""]
    return "\n".join(lines + ["data: [DONE]", ""])


def delta(**kwargs):
    return {"choices": [{"delta": kwargs}]}


def make_client(llm, keys=("k1",), **kwargs):
    kwargs.setdefault("reasoning", "off")
    return Client(base_url=llm.url, pool=KeyPool(list(keys)), **kwargs)


def test_stream_collects_text_and_filters_thoughts(llm):
    llm.script.append(("sse", sse(
        delta(content="<thou"), delta(content="ght>plan</thought>Hel"),
        {"choices": []}, {"choices": None}, "not json", {"nochoices": True},
        delta(content="lo "), delta(content=None), delta(content="world<th"),
    )))
    seen = []
    reply = make_client(llm).chat([{"role": "user", "content": "hi"}], on_token=seen.append)
    assert reply == {"role": "assistant", "content": "Hello world<th"}
    assert "".join(seen) == "Hello world<th"
    request = llm.requests[0]
    assert request["auth"] == "Bearer k1"
    assert request["path"] == "/v1/chat/completions"
    assert request["body"]["stream"] is True and "tools" not in request["body"]


def test_stream_assembles_tool_calls(llm):
    llm.script.append(("sse", sse(
        delta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "days_", "arguments": '{"da'}}]),
        delta(tool_calls=[{"index": 0, "function": {"name": "until", "arguments": 'te":"2026-11-11"}'}}]),
        delta(tool_calls=[{"id": "c2", "function": {"name": "add_days"}}]),
        delta(tool_calls=[{"function": {"arguments": "{}"}}]),
        delta(tool_calls=[{"index": 5}]),
    )))
    reply = make_client(llm).chat([], tools=[{"type": "function"}])
    calls = reply["tool_calls"]
    assert calls[0] == {"id": "c1", "type": "function",
                        "function": {"name": "days_until", "arguments": '{"date":"2026-11-11"}'}}
    assert calls[1]["id"] == "c2" and calls[1]["function"]["name"] == "add_days"
    assert calls[2]["function"]["arguments"] == "{}"
    assert len(calls) == 4
    assert llm.requests[0]["body"]["tools"] == [{"type": "function"}]


def test_json_response_path(llm):
    message = {"content": "<think>x</think>Answer", "tool_calls": [{"id": "1"}]}
    llm.script.append(("json", json.dumps({"choices": [{"message": message}]})))
    seen = []
    reply = make_client(llm).chat([], on_token=seen.append)
    assert reply == {"role": "assistant", "content": "Answer", "tool_calls": [{"id": "1"}]}
    assert seen == ["Answer"]


def test_json_response_without_content_or_callback(llm):
    llm.script.append(("json", json.dumps({"choices": [{"message": {"content": None}}]})))
    assert make_client(llm).chat([]) == {"role": "assistant", "content": ""}


def test_client_error_message_comes_from_body(llm):
    body = json.dumps({"error": {"message": "bad request body"}})
    llm.script.append(("error", (400, body)))
    with pytest.raises(ApiError, match="bad request body") as info:
        make_client(llm).chat([])
    assert info.value.code == 400


def test_server_errors_are_retried_with_backoff(llm, no_sleep):
    llm.script += [("error", (503, "{}")), ("error", (429, "{}")),
                   ("json", json.dumps({"choices": [{"message": {"content": "ok"}}]}))]
    assert make_client(llm).chat([])["content"] == "ok"
    assert no_sleep == pytest.approx([2, 4], abs=0.1)


def test_retries_stop_after_the_limit(llm, no_sleep):
    llm.script += [("error", (500, "{}"))] * 4
    with pytest.raises(ApiError) as info:
        make_client(llm).chat([])
    assert info.value.code == 500 and len(llm.requests) == 4


def test_rejected_key_rotates_to_the_next_key(llm, no_sleep):
    llm.script += [("error", (403, "{}")),
                   ("json", json.dumps({"choices": [{"message": {"content": "ok"}}]}))]
    assert make_client(llm, keys=("bad", "good")).chat([])["content"] == "ok"
    assert [r["auth"] for r in llm.requests] == ["Bearer bad", "Bearer good"]
    assert no_sleep == []


def test_rejected_single_key_is_not_retried(llm):
    llm.script.append(("error", (401, json.dumps([{"error": {"message": "key invalid"}}]))))
    with pytest.raises(ApiError, match="key invalid"):
        make_client(llm).chat([])
    assert len(llm.requests) == 1


def test_missing_api_key_gives_a_helpful_error(llm):
    with pytest.raises(ApiError, match="--offline"):
        make_client(llm, keys=()).chat([])
    assert llm.requests == []


def test_offline_client_sends_no_authorization(llm):
    llm.script.append(("json", json.dumps({"choices": [{"message": {"content": "local"}}]})))
    client = Client(base_url=llm.url, offline=True)
    assert client.model == agent.LOCAL_MODEL
    assert client.chat([])["content"] == "local"
    assert llm.requests[0]["auth"] is None


def test_unreachable_server_is_reported(no_sleep):
    cloud = Client(base_url="http://127.0.0.1:9/v1", pool=KeyPool(["k"]), timeout=2)
    with pytest.raises(ApiError, match="Cannot reach the model service") as info:
        cloud.chat([])
    assert info.value.code == 0
    local = Client(base_url="http://127.0.0.1:9/v1", offline=True, timeout=2)
    with pytest.raises(ApiError, match="Cannot reach the local model"):
        local.chat([])


def test_client_defaults(monkeypatch):
    monkeypatch.setenv("GEMINI_1_KEY", "abc")
    client = Client()
    assert client.pool.all() == ["abc"]
    assert client.model == agent.MODEL
    assert client.base_url == agent.BASE_URL.rstrip("/")
    assert Client(offline=True).base_url == agent.LOCAL_URL.rstrip("/")
    assert Client(model="m", base_url="http://x/", offline=True).model == "m"


def http_error(code, body, reason="Bad"):
    class Body:
        def close(self):
            pass

        def read(self):
            if isinstance(body, Exception):
                raise body
            return body

    return urllib.error.HTTPError("http://x", code, reason, {}, Body())


@pytest.mark.parametrize(
    "body, expected",
    [
        (b'{"error": {"message": "nope"}}', "nope"),
        (b'[{"error": {"message": "listed"}}]', "listed"),
        (b'{"error": "plain"}', "plain"),
        (b'{"other": 1}', "Bad"),
        (b"[]", "Bad"),
        (b"<html>", "Bad"),
        (b'"text"', "Bad"),
        (OSError("closed"), "Bad"),
    ],
)
def test_api_error_from(body, expected):
    err = agent.api_error_from(http_error(418, body))
    assert (err.code, str(err)) == (418, expected)


def test_api_error_falls_back_to_generic_text():
    assert str(agent.api_error_from(http_error(500, b"{}", reason=""))) == "request failed"


def test_thought_filter_variants():
    def run(*parts):
        filt = ThoughtFilter()
        return "".join(filt.feed(p) for p in parts) + filt.flush()

    assert run("plain text") == "plain text"
    assert run("a<thought>x</thought>b") == "ab"
    assert run("a<think>x</think>b<thought>y</thought>c") == "abc"
    assert run("a<thought>x</tho", "ught>b") == "ab"
    assert run("a<thought>never closed") == "a"
    assert run("1 < 2 and <th") == "1 < 2 and <th"
    assert run("<", "think>hidden</think>shown") == "shown"
    assert run("") == ""
    assert strip_thoughts("<thought>x</thought>  hi \n") == "hi"
    assert strip_thoughts(None) == ""


def test_read_stream_without_done_marker_and_empty_deltas():
    lines = [
        b'data: {"choices": [{"delta": {"content": ""}}]}\n',
        b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n',
    ]
    seen = []
    reply = agent.Client._read_stream(lines, seen.append)
    assert reply["content"] == "Hi" and seen == ["Hi"]


def test_read_stream_works_without_a_token_callback():
    lines = [b'data: {"choices": [{"delta": {"content": "Quiet"}}]}\n']
    assert agent.Client._read_stream(lines, None)["content"] == "Quiet"


def ok_reply(text="ok"):
    return ("json", json.dumps({"choices": [{"message": {"content": text}}]}))


def test_rate_limited_key_fails_over_to_the_other_key_without_waiting(llm, no_sleep):
    llm.script += [("error", (429, "{}")), ok_reply()]
    assert make_client(llm, keys=("a", "b")).chat([])["content"] == "ok"
    assert [r["auth"] for r in llm.requests] == ["Bearer a", "Bearer b"]
    assert no_sleep == []


def test_cooled_down_key_is_skipped_on_the_next_request(llm, no_sleep):
    llm.script += [("error", (429, "{}")), ok_reply(), ok_reply(), ok_reply()]
    client = make_client(llm, keys=("a", "b"))
    for _ in range(3):
        client.chat([])
    assert [r["auth"] for r in llm.requests] == ["Bearer a", "Bearer b", "Bearer b", "Bearer b"]


def test_requests_alternate_between_healthy_keys(llm):
    llm.script += [ok_reply() for _ in range(4)]
    client = make_client(llm, keys=("a", "b"))
    for _ in range(4):
        client.chat([])
    assert [r["auth"] for r in llm.requests] == ["Bearer a", "Bearer b"] * 2


def test_retry_after_header_sets_the_cooldown(monkeypatch):
    err = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "7"}, io.BytesIO(b"{}"))
    assert agent.api_error_from(err).retry_after == 7
    err = urllib.error.HTTPError("u", 429, "Too Many", {"Retry-After": "soon"}, io.BytesIO(b"{}"))
    assert agent.api_error_from(err).retry_after is None
    err = urllib.error.HTTPError("u", 429, "Too Many", {}, io.BytesIO(b"{}"))
    assert agent.api_error_from(err).retry_after is None


def test_all_keys_limited_waits_for_the_shortest_cooldown(llm, no_sleep):
    llm.script += [("error", (429, "{}")), ("error", (429, "{}")), ok_reply()]
    client = make_client(llm, keys=("a", "b"))
    assert client.chat([])["content"] == "ok"
    assert len(no_sleep) == 1 and 0 < no_sleep[0] <= 4


def test_rejected_keys_are_benched_not_waited_on(llm, no_sleep):
    llm.script += [("error", (403, "{}")), ("error", (403, "{}")), ("error", (403, "{}")),
                   ("error", (403, "{}"))]
    with pytest.raises(ApiError):
        make_client(llm, keys=("a", "b")).chat([])
    assert no_sleep == []


def test_reasoning_effort_is_sent_and_dropped_when_rejected(llm):
    llm.script += [("error", (400, json.dumps({"error": {"message": "unknown field"}}))),
                   ok_reply(), ok_reply()]
    client = make_client(llm, reasoning="low")
    assert client.chat([])["content"] == "ok"
    assert llm.requests[0]["body"]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in llm.requests[1]["body"]
    client.chat([])
    assert "reasoning_effort" not in llm.requests[2]["body"]


def test_bad_request_without_reasoning_is_not_retried(llm):
    llm.script.append(("error", (400, "{}")))
    with pytest.raises(ApiError):
        make_client(llm).chat([])
    assert len(llm.requests) == 1


def test_reasoning_defaults(monkeypatch):
    assert Client(offline=True).reasoning == "low"
    monkeypatch.setenv("LETTERLENS_REASONING", "off")
    assert Client(offline=True).reasoning is None
    monkeypatch.setenv("LETTERLENS_REASONING", "medium")
    assert Client(offline=True).reasoning == "medium"
    assert Client(offline=True, reasoning="").reasoning is None


def test_keys_are_released_after_streaming(llm):
    llm.script += [ok_reply()]
    client = make_client(llm, keys=("a",))
    client.chat([])
    assert client.pool._busy["a"] == 0
