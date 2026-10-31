import io
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

import app


class TestLetterLens(unittest.TestCase):
    def test_thought_text_is_removed_from_the_reply(self):
        with patch.object(app.Client, "chat", lambda self, m, tools=None, on_token=None: {"role": "assistant", "content": "<thought>plan</thought>pong"}):
            seen = []
            reply = app.GeminiClient(api_key="x").chat([], on_token=seen.append)
            self.assertEqual(reply["content"], "pong")
            self.assertEqual(seen, ["pong"])

    def test_server_errors_are_retried_but_client_errors_are_not(self):
        with patch.object(app.time, "sleep", lambda s: None):
            calls = []

            def flaky(self, m, tools=None, on_token=None):
                calls.append(1)
                if len(calls) < 3:
                    raise urllib.error.HTTPError("u", 500, "x", {}, None)
                return {"role": "assistant", "content": "ok"}

            with patch.object(app.Client, "chat", flaky):
                self.assertEqual(app.GeminiClient(api_key="x").chat([])["content"], "ok")
                self.assertEqual(len(calls), 3)

            def bad(self, m, tools=None, on_token=None):
                raise urllib.error.HTTPError("u", 400, "x", {}, None)

            with patch.object(app.Client, "chat", bad):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    app.GeminiClient(api_key="x").chat([])
                self.assertEqual(ctx.exception.code, 400)

    def test_the_agent_answers_a_letter(self):
        class Fake:
            def chat(self, messages, tools=None, on_token=None):
                return {"role": "assistant", "content": "pay by Friday"}

        self.assertEqual(app.build_agent(Fake()).run("Explain this letter: ..."), "pay by Friday")

    def test_download_button_and_zip(self):
        server = app.make_app_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        self.assertIn(b"/letterlens.zip", urllib.request.urlopen(base + "/").read())
        r = urllib.request.urlopen(base + "/letterlens.zip")
        self.assertIn("attachment", r.headers["Content-Disposition"])
        names = zipfile.ZipFile(io.BytesIO(r.read())).namelist()
        server.shutdown()
        self.assertTrue("letterlens/app.py" in names and "letterlens/LICENSE" in names)
        self.assertFalse([n for n in names if ".venv" in n or ".git/" in n or "__pycache__" in n])

    def test_agent_handles_markdown_files(self):
        recorded = []

        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                recorded.append(messages[-1])
                return {"role": "assistant", "content": "Summary of rent increase"}

        agent_inst = app.Agent(FakeClient())
        res = agent_inst.run(
            prompt="Explain this notice",
            files=[{"name": "notice.md", "content": "# Rent Increase Notice"}]
        )
        self.assertEqual(res, "Summary of rent increase")
        self.assertIn("notice.md", recorded[0]["content"])
        self.assertIn("# Rent Increase Notice", recorded[0]["content"])

    def test_agent_handles_pictures_multimodal(self):
        recorded = []

        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                recorded.append(messages[-1])
                return {"role": "assistant", "content": "Description of image"}

        agent_inst = app.Agent(FakeClient())
        res = agent_inst.run(
            prompt="What is this picture?",
            images=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="]
        )
        self.assertEqual(res, "Description of image")
        content = recorded[0]["content"]
        self.assertTrue(isinstance(content, list))
        self.assertTrue(any(part.get("type") == "image_url" for part in content))

    def test_key_pool_discovers_multiple_gemini_keys(self):
        pool = app.KeyPool()
        self.assertGreaterEqual(pool.size(), 2)
        k1 = pool.get_key()
        k2 = pool.get_key()
        self.assertTrue(bool(k1))
        self.assertTrue(bool(k2))

    def test_markdown_parser_extracts_footnotes(self):
        text = "Under municipal law[^1], you have 14 days.\n\n[^1]: Budapest Municipal Decree No. 14/2021"
        fields = app.MarkdownParser.extract_key_fields(text)
        self.assertEqual(len(fields["footnotes"]), 1)
        self.assertEqual(fields["footnotes"][0]["id"], "1")
        self.assertEqual(fields["footnotes"][0]["text"], "Budapest Municipal Decree No. 14/2021")
        terminal_out = app.MarkdownParser.to_terminal_text(text, use_color=False)
        self.assertIn("[1]", terminal_out)

    def test_agent_refuses_non_legal_queries(self):
        recorded = []

        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                recorded.append(messages[0]["content"])
                return {"role": "assistant", "content": "I can only assist with legal, administrative, and official documents, such as government notices, permits, tenancy agreements, contracts, and bills. Please provide a legal or official document."}

        agent_inst = app.Agent(FakeClient())
        res = agent_inst.run("Can you write me a poem about summer?")
        self.assertIn("Strict Legal Scope Requirement", recorded[0])
        self.assertIn("I can only assist with legal, administrative, and official documents", res)

    def test_country_legal_portals_repository_coverage_and_lookup(self):
        tool = app.country_legal_portal_tool()
        lookup = tool["fn"]
        res = lookup("Hungary")
        self.assertIn("magyarkozlony.hu", res)
        self.assertIn("oif.gov.hu", res)
        res_de = lookup("Germany")
        self.assertIn("gesetze-im-internet.de", res_de)
        res_us = lookup("United States")
        self.assertIn("federalregister.gov", res_us)
        res_jp = lookup("Japan")
        self.assertIn("elaws.e-gov.go.jp", res_jp)

    def test_country_legal_portal_aliases_and_listing(self):
        tool = app.country_legal_portal_tool()
        lookup = tool["fn"]
        res_alias = lookup("USA")
        self.assertIn("United States", res_alias)
        res_uk = lookup("UK")
        self.assertIn("legislation.gov.uk", res_uk)
        res_code = lookup("DE")
        self.assertIn("gesetze-im-internet.de", res_code)
        res_all = lookup("all")
        self.assertIn("total_countries", res_all)
        self.assertIn("Primary official legal repository loaded", res_all)

    def test_agent_registers_country_legal_portal_tool(self):
        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                return {"role": "assistant", "content": "Analysis complete"}

        agent_inst = app.build_agent(FakeClient())
        self.assertIn("lookup_country_legal_portal", agent_inst.tools)
        tool_desc = agent_inst.tools["lookup_country_legal_portal"]["schema"]["function"]["description"]
        self.assertIn("Primary Source of Information", tool_desc)


def test_thought_text_is_removed_from_the_reply(monkeypatch=None):
    TestLetterLens().test_thought_text_is_removed_from_the_reply()


def test_server_errors_are_retried_but_client_errors_are_not(monkeypatch=None):
    TestLetterLens().test_server_errors_are_retried_but_client_errors_are_not()


def test_the_agent_answers_a_letter():
    TestLetterLens().test_the_agent_answers_a_letter()


def test_download_button_and_zip():
    TestLetterLens().test_download_button_and_zip()


def test_agent_handles_markdown_files():
    TestLetterLens().test_agent_handles_markdown_files()


def test_agent_handles_pictures_multimodal():
    TestLetterLens().test_agent_handles_pictures_multimodal()


def test_markdown_parser_extracts_footnotes():
    TestLetterLens().test_markdown_parser_extracts_footnotes()


def test_agent_refuses_non_legal_queries():
    TestLetterLens().test_agent_refuses_non_legal_queries()


def test_country_legal_portals_repository_coverage_and_lookup():
    TestLetterLens().test_country_legal_portals_repository_coverage_and_lookup()


def test_country_legal_portal_aliases_and_listing():
    TestLetterLens().test_country_legal_portal_aliases_and_listing()


def test_agent_registers_country_legal_portal_tool():
    TestLetterLens().test_agent_registers_country_legal_portal_tool()


if __name__ == "__main__":
    unittest.main()
