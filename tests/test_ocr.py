import subprocess
import sys
import types

import pytest

import ocr
from ocr import LocalOCR


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(LocalOCR, "_cache", {})
    monkeypatch.delenv("LETTERLENS_OCR", raising=False)


def fake_platform(monkeypatch, name):
    monkeypatch.setattr(ocr, "sys", types.SimpleNamespace(platform=name))


def test_engine_off_via_env(monkeypatch):
    monkeypatch.setenv("LETTERLENS_OCR", "OFF")
    assert LocalOCR.engine() == "off"


def test_engine_windows(monkeypatch):
    fake_platform(monkeypatch, "win32")
    assert LocalOCR.engine() == "windows"


def test_engine_tesseract_and_none(monkeypatch):
    fake_platform(monkeypatch, "linux")
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: object())
    assert LocalOCR.engine() == "tesseract"
    monkeypatch.setattr(ocr.importlib.util, "find_spec", lambda name: None)
    assert LocalOCR.engine() == "none"
    monkeypatch.setattr(ocr.importlib.util, "find_spec",
                        lambda name: object() if name == "pytesseract" else None)
    assert LocalOCR.engine() == "none"


def test_extract_text_skips_when_disabled_or_empty(monkeypatch):
    monkeypatch.setattr(LocalOCR, "engine", classmethod(lambda cls: "none"))
    assert LocalOCR.extract_text(b"abc") == ""
    monkeypatch.setattr(LocalOCR, "engine", classmethod(lambda cls: "windows"))
    assert LocalOCR.extract_text(b"") == ""


def test_extract_text_caches_and_evicts(monkeypatch):
    calls = []
    monkeypatch.setattr(LocalOCR, "engine", classmethod(lambda cls: "windows"))
    monkeypatch.setattr(LocalOCR, "_run", classmethod(lambda cls, data, t: calls.append(data) or "txt"))
    assert LocalOCR.extract_text(b"one") == "txt"
    assert LocalOCR.extract_text(b"one") == "txt"
    assert calls == [b"one"]
    for number in range(40):
        LocalOCR.extract_text(str(number).encode())
    assert len(LocalOCR._cache) == 32


def test_dir_is_recreated(monkeypatch, tmp_path):
    monkeypatch.setattr(LocalOCR, "_workdir", str(tmp_path / "gone"))
    created = LocalOCR._dir()
    assert created != str(tmp_path / "gone")
    assert LocalOCR._dir() == created


def test_run_on_windows_prefers_native_then_tesseract(monkeypatch):
    fake_platform(monkeypatch, "win32")
    monkeypatch.setattr(LocalOCR, "_windows", classmethod(lambda cls, path, t: "native"))
    monkeypatch.setattr(LocalOCR, "_tesseract", classmethod(lambda cls, path: "tess"))
    assert LocalOCR._run(b"x", 5) == "native"
    monkeypatch.setattr(LocalOCR, "_windows", classmethod(lambda cls, path, t: ""))
    assert LocalOCR._run(b"x", 5) == "tess"


def test_run_off_windows_uses_tesseract_and_cleans_up(monkeypatch):
    fake_platform(monkeypatch, "linux")
    seen = []
    monkeypatch.setattr(LocalOCR, "_tesseract", classmethod(lambda cls, path: seen.append(path) or "t"))
    assert LocalOCR._run(b"x", 5) == "t"
    import os
    assert not os.path.exists(seen[0])


def run_result(code=0, out="", err=""):
    return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)


def test_windows_success_passes_language(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return run_result(0, " hello \n")

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    monkeypatch.setenv("LETTERLENS_OCR_LANG", "hu")
    assert LocalOCR._windows("img.png", 5) == "hello"
    assert seen["command"][-2:] == ["img.png", "hu"]
    assert seen["command"][0] == "powershell"


def test_windows_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **k: run_result(1, "", "boom"))
    assert LocalOCR._windows("img.png", 5) == ""
    assert "boom" in caplog.text


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("p", 1)])
def test_windows_start_errors(monkeypatch, caplog, error):
    def explode(*a, **k):
        raise error

    monkeypatch.setattr(ocr.subprocess, "run", explode)
    assert LocalOCR._windows("img.png", 5) == ""
    assert "did not run" in caplog.text


class FakeImageModule:
    class _Ctx:
        def __enter__(self):
            return "image-object"

        def __exit__(self, *exc):
            return False

    @classmethod
    def open(cls, path):
        return cls._Ctx()


def install_fakes(monkeypatch, text="  words  ", fail=False):
    pillow = types.ModuleType("PIL")
    pillow.Image = FakeImageModule
    tess = types.ModuleType("pytesseract")
    seen = {}

    def image_to_string(img, lang=None):
        if fail:
            raise RuntimeError("no tesseract binary")
        seen.update(img=img, lang=lang)
        return text

    tess.image_to_string = image_to_string
    monkeypatch.setitem(sys.modules, "PIL", pillow)
    monkeypatch.setitem(sys.modules, "pytesseract", tess)
    return seen


def test_tesseract_reads_text(monkeypatch):
    seen = install_fakes(monkeypatch)
    monkeypatch.setenv("LETTERLENS_TESSERACT_LANG", "hun")
    assert LocalOCR._tesseract("x.png") == "words"
    assert seen == {"img": "image-object", "lang": "hun"}


def test_tesseract_default_language_and_failure(monkeypatch, caplog):
    seen = install_fakes(monkeypatch)
    LocalOCR._tesseract("x.png")
    assert seen["lang"] is None
    install_fakes(monkeypatch, fail=True)
    assert LocalOCR._tesseract("x.png") == ""
    assert "did not run" in caplog.text
