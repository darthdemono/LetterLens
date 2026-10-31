import threading

import pytest

import app
from agent import ApiError, GemmaAgent

sync_api = pytest.importorskip("playwright.sync_api")


class Broken:
    offline = False

    def __init__(self, error):
        self.error = error

    def chat(self, messages, tools=None, on_token=None):
        if self.error:
            raise self.error
        on_token("## At a glance\n\nAll good.")
        return {"role": "assistant", "content": "## At a glance\n\nAll good."}


@pytest.fixture
def page_for():
    servers = []
    with sync_api.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception:
            pytest.skip("chromium is not installed")

        def open_page(error):
            server = app.make_app_server(port=0, host="127.0.0.1", offline=False,
                                         factory=lambda: GemmaAgent(Broken(error)))
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02},
                             daemon=True).start()
            servers.append(server)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            return page

        yield open_page
        browser.close()
    for server in servers:
        server.shutdown()
        server.server_close()


def ask(page):
    page.fill("#userInput", "hello there")
    page.keyboard.press("Enter")


def test_errors_from_the_model_are_shown_to_the_person(page_for):
    page = page_for(ApiError(429, "quota exceeded"))
    ask(page)
    page.wait_for_selector(".notice-error")
    assert "out of quota" in page.inner_text(".notice-error")
    assert "quota exceeded" in page.inner_text(".notice-error")


def test_a_good_answer_shows_the_time_taken(page_for):
    page = page_for(None)
    ask(page)
    page.wait_for_selector(".took")
    assert page.inner_text(".took").startswith("Answered in ")
    assert "All good." in page.inner_text(".msg.agent")
