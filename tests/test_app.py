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

    def test_generic_ai_questions_are_allowed_and_answered(self):
        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                return {"role": "assistant", "content": "not called"}

        agent_inst = app.Agent(FakeClient())
        res = agent_inst.run("what can you do")
        self.assertIn("LetterLens Capabilities", res)
        self.assertIn("Local OCR Acceleration", res)
        res_help = agent_inst.run("help")
        self.assertIn("LetterLens Capabilities", res_help)

    def test_denial_of_acceptance_replies_past_prompt_results(self):
        calls = []

        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                calls.append(len(messages))
                if len(calls) == 1:
                    return {"role": "assistant", "content": "### Summary\nLease breakdown of 500 EUR."}
                return {
                    "role": "assistant",
                    "content": "I can only assist with legal, administrative, and official documents, such as government notices, permits, tenancy agreements, contracts, and bills. Please provide a legal or official document."
                }

        agent_inst = app.Agent(FakeClient())
        first_res = agent_inst.run("Explain this lease with 500 EUR rent")
        self.assertIn("Lease breakdown of 500 EUR", first_res)

        second_res = agent_inst.run("Write a poem about puppies")
        self.assertIn("I can only assist with legal", second_res)
        self.assertIn("Previous Document Analysis", second_res)
        self.assertIn("Lease breakdown of 500 EUR", second_res)

    def test_footnotes_hyperlink_display_format(self):
        text = "Under federal tenancy rules[^1], rent caps apply.\n\n1. https://www.gesetze-im-internet.de - Federal Ministry of Justice\n2. https://www.bmwsb.bund.de - Housing Ministry"
        fields = app.MarkdownParser.extract_key_fields(text)
        self.assertEqual(len(fields["footnotes"]), 2)
        self.assertEqual(fields["footnotes"][0]["id"], "1")
        self.assertIn("https://www.gesetze-im-internet.de", fields["footnotes"][0]["text"])

    def test_static_files_serving(self):
        server = app.make_app_server(port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        r_html = urllib.request.urlopen(base + "/")
        self.assertEqual(r_html.status, 200)
        self.assertIn(b"LetterLens", r_html.read())

        r_css = urllib.request.urlopen(base + "/styles.css")
        self.assertEqual(r_css.status, 200)
        self.assertIn(b"--accent", r_css.read())

        r_js = urllib.request.urlopen(base + "/app.js")
        self.assertEqual(r_js.status, 200)
        self.assertIn(b"parseMarkdown", r_js.read())

        r_api = urllib.request.urlopen(base + "/api/countries")
        self.assertEqual(r_api.status, 200)
        self.assertIn(b"Hungary", r_api.read())
        server.shutdown()


    def test_country_settings_injects_into_context_window(self):
        recorded = []

        class FakeClient:
            def chat(self, messages, tools=None, on_token=None):
                recorded.extend(messages)
                return {"role": "assistant", "content": "Analysis for Germany"}

        agent_inst = app.Agent(FakeClient())
        agent_inst.set_country_context("Germany")
        self.assertEqual(agent_inst.country, "Germany")
        found_context = any(
            m.get("role") == "system" and "gesetze-im-internet.de" in m.get("content", "")
            for m in agent_inst.messages
        )
        self.assertTrue(found_context)

        agent_inst.run("Explain notice", country="France")
        self.assertEqual(agent_inst.country, "France")
        found_france = any(
            m.get("role") == "system" and "legifrance.gouv.fr" in m.get("content", "")
            for m in agent_inst.messages
        )
        self.assertTrue(found_france)


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


def test_generic_ai_questions_are_allowed_and_answered():
    TestLetterLens().test_generic_ai_questions_are_allowed_and_answered()


def test_denial_of_acceptance_replies_past_prompt_results():
    TestLetterLens().test_denial_of_acceptance_replies_past_prompt_results()


def test_footnotes_hyperlink_display_format():
    TestLetterLens().test_footnotes_hyperlink_display_format()


def test_static_files_serving():
    TestLetterLens().test_static_files_serving()


def test_country_settings_injects_into_context_window():
    TestLetterLens().test_country_settings_injects_into_context_window()


if __name__ == "__main__":
    unittest.main()
