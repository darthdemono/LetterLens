import datetime
import json
import runpy
import sys

import pytest

import agent
import tools
from agent import ApiError, GemmaAgent
from images import Image


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(tools, "today", lambda: datetime.date(2026, 10, 31))


class Script:
    offline = False

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, on_token=None):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools})
        reply = self.replies.pop(0)
        if callable(reply):
            reply = reply(on_token)
        elif on_token and reply.get("content"):
            on_token(reply["content"])
        return reply


def say(text):
    return {"role": "assistant", "content": text}


def tool_call(name, args, call_id="c1"):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]}


def test_system_prompt_has_date_and_scope():
    bot = GemmaAgent(Script(say("ok")))
    system = bot.messages[0]["content"]
    assert "Today's date is 2026-10-31" in system
    assert agent.REFUSAL in system
    assert "{" not in system.replace("{}", "")


def test_run_returns_reply_and_streams_events():
    client = Script(say("pay by Friday"))
    events, tokens = [], []
    bot = GemmaAgent(client)
    reply = bot.run("Explain this letter", on_token=tokens.append, on_event=events.append)
    assert reply == "pay by Friday"
    assert tokens == ["pay by Friday"] and events == [{"t": "pay by Friday"}]
    assert client.calls[0]["tools"] is None


def test_custom_system_prompt_is_used():
    bot = GemmaAgent(Script(), system="Custom {today} {refusal}{country} {unknown}")
    assert bot.messages[0]["content"].startswith("Custom 2026-10-31 I can only help")
    assert "{unknown}" in bot.messages[0]["content"]


def test_default_client_is_created(monkeypatch):
    monkeypatch.setenv("GEMINI_1_KEY", "k")
    assert GemmaAgent().client.pool.all() == ["k"]


def test_country_context_is_replaced_not_stacked():
    bot = GemmaAgent(Script(say("a"), say("b"), say("c")), country="Hungary")
    assert bot.country == "Hungary" and "njt.hu" in bot.messages[0]["content"]
    bot.run("one", country="Hungary")
    bot.run("two", country="Germany")
    assert "gesetze-im-internet.de" in bot.messages[0]["content"]
    assert "njt.hu" not in bot.messages[0]["content"]
    assert [m["role"] for m in bot.messages].count("system") == 1
    bot.run("three", country="")
    assert bot.country is None and "chose a country" not in bot.messages[0]["content"]
    bot.set_country_context("Auto")
    bot.set_country_context(None)


def test_country_none_keeps_current_setting():
    bot = GemmaAgent(Script(say("a")), country="Hungary")
    bot.run("one")
    assert bot.country == "Hungary"


def test_generic_questions_skip_the_model():
    client = Script()
    bot = GemmaAgent(client)
    tokens = []
    assert bot.run("What can you do?", on_token=tokens.append) == agent.GENERIC_AI_RESPONSE
    assert tokens == [agent.GENERIC_AI_RESPONSE]
    assert bot.run("help") == agent.GENERIC_AI_RESPONSE
    assert client.calls == []


def test_generic_question_with_attachment_goes_to_the_model():
    client = Script(say("done"))
    GemmaAgent(client).run("help", files=[{"name": "a.txt", "content": "x"}])
    assert len(client.calls) == 1


def test_files_are_added_and_truncated():
    client = Script(say("ok"))
    bot = GemmaAgent(client)
    big = "x" * (agent.MAX_FILE_CHARS + 10)
    bot.run(None, files=[{"name": "notice.md", "content": "# Rent"}, {"content": big}])
    sent = client.calls[0]["messages"][-1]["content"]
    assert "--- Document: notice.md ---\n# Rent" in sent
    assert "--- Document: document ---" in sent and "[truncated]" in sent
    assert sent.endswith("Explain this document.")


def test_images_use_ocr_and_become_parts(monkeypatch):
    monkeypatch.setattr(agent.LocalOCR, "extract_text",
                        staticmethod(lambda data: "OCR " + data.decode()))
    client = Script(say("ok"))
    bot = GemmaAgent(client)
    png = Image("image/png", b"one")
    bot.run("Explain", images=[png, b"two", "data:image/jpeg;base64,dGhyZWU="])
    content = client.calls[0]["messages"][-1]["content"]
    assert content[0]["type"] == "text" and "OCR one" in content[0]["text"]
    assert "image 3" in content[0]["text"]
    urls = [p["image_url"]["url"] for p in content[1:]]
    assert urls[0].startswith("data:image/png;base64,") and urls[2].startswith("data:image/jpeg")


def test_blank_ocr_is_skipped(monkeypatch):
    monkeypatch.setattr(agent.LocalOCR, "extract_text", staticmethod(lambda data: ""))
    client = Script(say("ok"))
    GemmaAgent(client).run("Explain", images=[b"x"])
    assert "local OCR" not in client.calls[0]["messages"][-1]["content"][0]["text"]


def test_local_paths_are_rejected_as_images():
    with pytest.raises(ValueError):
        GemmaAgent(Script()).run("x", images=["/etc/passwd"])


def test_history_is_trimmed_and_old_images_removed():
    client = Script(*[say(f"r{i}") for i in range(5)])
    bot = GemmaAgent(client, max_turns=3)
    bot.run("first", images=[b"img"])
    bot.run("second")
    bot.run("third")
    first_after = [m for m in bot.messages if m["role"] == "user"][0]["content"]
    assert first_after == "first\n[image removed to save space]"
    bot.run("fourth")
    bot.run("fifth")
    users = [m["content"] for m in bot.messages if m["role"] == "user"]
    assert users == ["third", "fourth", "fifth"]
    assert bot.messages[0]["role"] == "system"


def test_tool_loop_runs_tools_and_reports_status():
    streamed = []

    def thinking(on_token):
        on_token("Let me check. ")
        return tool_call("days_until", {"date": "2026-11-11"})

    client = Script(thinking, say("11 days left"))
    bot = GemmaAgent(client)
    bot.add_tools(tools.date_tools())
    events = []
    assert bot.run("When?", on_token=streamed.append, on_event=events.append) == "11 days left"
    assert {"reset": True} in events and {"status": "Using days_until"} in events
    result = [m for m in bot.messages if m["role"] == "tool"][0]
    assert json.loads(result["content"])["days_left"] == 11
    assert client.calls[0]["tools"][0]["function"]["name"] == "days_until"


def test_tool_loop_without_callbacks_or_streaming():
    client = Script(tool_call("days_until", {"date": "2026-11-11"}, call_id=""), say("ok"))
    bot = GemmaAgent(client)
    bot.add_tools(tools.date_tools())
    assert bot.run("When?") == "ok"
    assert [m for m in bot.messages if m["role"] == "tool"][0]["tool_call_id"] == "days_until"


@pytest.mark.parametrize(
    "call, expected",
    [
        (tool_call("nope", {}), "error: unknown tool nope"),
        (tool_call("days_until", "{broken"), "error: Expecting"),
        (tool_call("days_until", {"wrong": 1}), "error: "),
        (tool_call("days_until", ""), "error: "),
        (tool_call("read_file", {"path": "missing.md"}), "error: file not found"),
        ({"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]}, "error: unknown tool"),
    ],
)
def test_tool_failures_become_messages_not_crashes(call, expected):
    client = Script(call, say("recovered"))
    bot = GemmaAgent(client)
    bot.add_tools(tools.date_tools())
    bot.add_tools(tools.folder_tools(agent.ROOT + "/rules"))
    assert bot.run("go") == "recovered"
    assert [m for m in bot.messages if m["role"] == "tool"][0]["content"].startswith(expected)


def test_endless_tool_calls_end_with_a_note():
    client = Script(*[tool_call("days_until", {"date": "2026-11-11"})] * 3)
    bot = GemmaAgent(client, max_steps=3)
    bot.add_tools(tools.date_tools())
    tokens = []
    reply = bot.run("loop", on_token=tokens.append)
    assert "could not finish" in reply and tokens == [reply]
    assert bot.messages[-1] == {"role": "assistant", "content": reply}


def test_empty_model_reply_returns_empty_string():
    assert GemmaAgent(Script({"role": "assistant"})).run("x") == ""


def test_unverified_quotes_trigger_a_warning():
    reply = "### Money\n> You owe 99,000 HUF today\n> rent is 120,000 HUF\n> ok"
    client = Script(say(reply))
    events = []
    GemmaAgent(client).run("Letter: rent is 120,000 HUF", on_event=events.append)
    warning = [e for e in events if "warn" in e][0]["warn"]
    assert "99,000" in warning and "120,000" not in warning


def test_verified_quotes_and_missing_callback_are_quiet():
    client = Script(say("> rent is 120,000 HUF"), say("> invented line of text"))
    bot = GemmaAgent(client)
    events = []
    bot.run("rent is 120,000 HUF", on_event=events.append)
    assert not [e for e in events if "warn" in e]
    assert bot.run("again") == "> invented line of text"


def test_unverified_quotes_helper():
    source = "Pay HUF 4,500 by November 15."
    assert agent.unverified_quotes("> pay HUF 4,500 by november 15!", source) == []
    assert agent.unverified_quotes('> "Pay HUF 9,999"', source) == ["Pay HUF 9,999"]
    assert agent.unverified_quotes("no quotes here", source) == []


def test_small_rules_are_inlined_without_file_tools():
    bot = agent.build_agent(Script())
    assert set(bot.tools) == {"lookup_country_legal_portal", "days_until", "add_days"}
    assert "Rule notes you can rely on" in bot.messages[0]["content"]
    assert "hiánypótlás" in bot.messages[0]["content"]


def test_large_rules_use_file_tools(tmp_path):
    (tmp_path / "big.md").write_text("word " * 3000, encoding="utf-8")
    bot = agent.build_agent(Script(), rules_dir=str(tmp_path))
    assert set(bot.tools) == {"lookup_country_legal_portal", "days_until", "add_days",
                              "list_files", "read_file", "search_docs"}
    assert "Rule notes" not in bot.messages[0]["content"]


def test_missing_rules_folder_means_no_notes_and_no_file_tools():
    bot = agent.build_agent(Script(), rules_dir="/does/not/exist")
    assert bot.tools.keys() == {"lookup_country_legal_portal", "days_until", "add_days"}


def test_build_agent_respects_rules_env_and_offline(tmp_path, monkeypatch):
    (tmp_path / "x.md").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("LETTERLENS_RULES", str(tmp_path))
    bot = agent.build_agent(offline=True)
    assert bot.client.offline is True
    assert "hello" in bot.messages[0]["content"]


def test_date_check_block_is_added_to_the_prompt(monkeypatch):
    monkeypatch.setattr(tools, "today", lambda: datetime.date(2026, 10, 31))
    client = Script(say("ok"))
    GemmaAgent(client).run("Pay by 2026-11-11 please", files=[{"name": "a.txt",
                                                                "content": "Due 2026.11.15"}])
    text = client.calls[0]["messages"][-1]["content"]
    assert "Date check" in text
    assert "2026-11-11 (Wednesday): 11 days from today" in text
    assert "2026-11-15 (Sunday): 15 days from today" in text


@pytest.fixture
def sample_files(tmp_path):
    (tmp_path / "scan.png").write_bytes(b"png")
    (tmp_path / "note.md").write_text("# Notice", encoding="utf-8")
    (tmp_path / "letter.txt").write_text("Dear tenant", encoding="utf-8")
    (tmp_path / "doc.pdf").write_bytes(b"%PDF")
    (tmp_path / "noext").write_text("x", encoding="utf-8")
    return tmp_path


def run_describe(target, sample_files, prompt=None, country=None):
    client = Script(say("ok"))
    bot = GemmaAgent(client)
    tokens = []
    agent.describe(bot, target, prompt, on_token=tokens.append, country=country)
    return client.calls[0]["messages"][-1]["content"]


def test_describe_image_markdown_and_text(sample_files):
    content = run_describe(str(sample_files / "scan.png"), sample_files, country="Hungary")
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "# Notice" in run_describe(str(sample_files / "note.md"), sample_files)
    assert "Dear tenant" in run_describe(str(sample_files / "letter.txt"), sample_files)


def test_describe_plain_text_and_prompt(sample_files):
    assert run_describe("What if I miss a deadline?", sample_files) == "What if I miss a deadline?"
    assert run_describe("some text", sample_files, prompt="Summarise").startswith("Summarise\n\nsome")


def test_describe_rejects_unsupported_and_missing(sample_files):
    with pytest.raises(ValueError, match="Unsupported file type .pdf"):
        run_describe(str(sample_files / "doc.pdf"), sample_files)
    with pytest.raises(ValueError, match="without extension"):
        run_describe(str(sample_files / "noext"), sample_files)
    with pytest.raises(ValueError, match="File not found"):
        run_describe("missing-letter.png", sample_files)
    with pytest.raises(ValueError, match="File not found"):
        run_describe("scan.pdf", sample_files)


class FakeAgent:
    def __init__(self, error=None):
        self.error = error
        self.seen = []
        self.country = None

    def set_country_context(self, country):
        self.country = country

    def run(self, prompt, on_token=None, **kwargs):
        self.seen.append(prompt)
        if self.error:
            raise self.error
        on_token("answer")
        return "answer"


def feed_input(monkeypatch, *lines):
    items = iter(lines)

    def fake_input(prompt):
        item = next(items)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr("builtins.input", fake_input)


def test_interactive_shell(monkeypatch, capsys):
    bot = FakeAgent()
    feed_input(monkeypatch, "", "tell me about rent", "exit")
    agent.interactive_shell(bot)
    out = capsys.readouterr().out
    assert "answer" in out and "Bye." in out and bot.seen == ["tell me about rent"]


@pytest.mark.parametrize("ending", [EOFError(), KeyboardInterrupt(), "q"])
def test_interactive_shell_endings(monkeypatch, capsys, ending):
    feed_input(monkeypatch, ending)
    agent.interactive_shell(FakeAgent())
    assert "Bye." in capsys.readouterr().out


def test_interactive_shell_shows_errors(monkeypatch, capsys):
    feed_input(monkeypatch, "hello there", "quit")
    agent.interactive_shell(FakeAgent(error=ApiError(500, "model down")))
    assert "Error: model down" in capsys.readouterr().out


def patch_build(monkeypatch, bot):
    calls = {}

    def fake_build(client=None, offline=False, rules_dir=None):
        calls["offline"] = offline
        return bot

    monkeypatch.setattr(agent, "build_agent", fake_build)
    return calls


def test_main_runs_text(monkeypatch, capsys):
    bot = FakeAgent()
    calls = patch_build(monkeypatch, bot)
    assert agent.main(["what is a deposit", "--offline", "--country", "Hungary"]) == 0
    assert capsys.readouterr().out.strip() == "answer"
    assert calls["offline"] is True and bot.country == "Hungary"


def test_main_reports_errors(monkeypatch, capsys):
    patch_build(monkeypatch, FakeAgent(error=ApiError(0, "no key")))
    assert agent.main(["some words here"]) == 1
    assert "Error: no key" in capsys.readouterr().err


def test_main_without_target_starts_shell(monkeypatch):
    patch_build(monkeypatch, FakeAgent())
    started = []
    monkeypatch.setattr(agent, "interactive_shell", started.append)
    assert agent.main([]) == 0 and agent.main(["-i", "x"]) == 0
    assert len(started) == 2


def test_module_runs_as_script(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent.py", "--help"])
    with pytest.raises(SystemExit) as info:
        runpy.run_module("agent", run_name="__main__")
    assert info.value.code == 0


def test_took_formats_fast_and_slow_runs(monkeypatch):
    monkeypatch.setattr(agent.time, "perf_counter", lambda: 10.0)
    assert agent.took(9.58) == "(420 ms)"
    assert agent.took(5.75) == "(4.2 s)"


def test_cli_prints_time_taken(monkeypatch, capsys):
    monkeypatch.setattr(agent, "build_agent", lambda offline=False: GemmaAgent(Script(say("Hi"))))
    assert agent.main(["what is a deposit"]) == 0
    assert capsys.readouterr().err.strip().endswith(("ms)", "s)"))


def test_cli_check_reports_and_sets_exit_code(monkeypatch, capsys):
    class Fake:
        model = "m"
        base_url = "http://x"

    monkeypatch.setattr(agent, "Client", lambda offline=False: Fake())
    monkeypatch.setattr(agent, "check_keys", lambda client: 2)
    assert agent.main(["--check"]) == 0
    assert "model m at http://x" in capsys.readouterr().out
    monkeypatch.setattr(agent, "check_keys", lambda client: 0)
    assert agent.main(["--check", "--offline"]) == 1
