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


if __name__ == "__main__":
    unittest.main()
