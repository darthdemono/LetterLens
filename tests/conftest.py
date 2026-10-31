import os

import pytest


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        upper = name.upper()
        if upper.startswith("LETTERLENS_") or ("GEMINI" in upper and "KEY" in upper):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LETTERLENS_OCR", "off")
