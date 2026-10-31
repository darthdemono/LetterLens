import hashlib
import importlib.util
import logging
import os
import subprocess
import sys
import tempfile
import threading

from config import ROOT

log = logging.getLogger("letterlens.ocr")

OFF_VALUES = {"off", "0", "false", "no"}


class LocalOCR:
    _cache = {}
    _lock = threading.Lock()
    _workdir = None

    @classmethod
    def engine(cls):
        if os.environ.get("LETTERLENS_OCR", "").lower() in OFF_VALUES:
            return "off"
        if sys.platform.startswith("win"):
            return "windows"
        if importlib.util.find_spec("pytesseract") and importlib.util.find_spec("PIL"):
            return "tesseract"
        return "none"

    @classmethod
    def _dir(cls):
        with cls._lock:
            if cls._workdir is None or not os.path.isdir(cls._workdir):
                cls._workdir = tempfile.mkdtemp(prefix="letterlens-")
            return cls._workdir

    @classmethod
    def extract_text(cls, data, timeout=8.0):
        if cls.engine() in {"off", "none"} or not data:
            return ""
        digest = hashlib.sha256(data).hexdigest()
        with cls._lock:
            if digest in cls._cache:
                return cls._cache[digest]
        text = cls._run(data, timeout)
        with cls._lock:
            if len(cls._cache) >= 32:
                cls._cache.pop(next(iter(cls._cache)))
            cls._cache[digest] = text
        return text

    @classmethod
    def _run(cls, data, timeout):
        handle, path = tempfile.mkstemp(suffix=".img", dir=cls._dir())
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(data)
            text = ""
            if sys.platform.startswith("win"):
                text = cls._windows(path, timeout)
            return text or cls._tesseract(path)
        finally:
            os.unlink(path)

    @classmethod
    def _windows(cls, path, timeout):
        script = os.path.join(ROOT, "ocr_win.ps1")
        command = [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", script, path, os.environ.get("LETTERLENS_OCR_LANG", ""),
        ]
        try:
            done = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Windows OCR did not run: %s", exc)
            return ""
        if done.returncode != 0:
            log.warning("Windows OCR failed: %s", done.stderr.strip())
        return done.stdout.strip()

    @classmethod
    def _tesseract(cls, path):
        try:
            import pytesseract
            from PIL import Image

            lang = os.environ.get("LETTERLENS_TESSERACT_LANG") or None
            with Image.open(path) as img:
                return pytesseract.image_to_string(img, lang=lang).strip()
        except Exception as exc:
            log.warning("Tesseract OCR did not run: %s", exc)
            return ""
