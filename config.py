import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = "0.2.0"

MODEL = os.environ.get("LETTERLENS_MODEL", "gemma-4-26b-a4b-it")
BASE_URL = os.environ.get(
    "LETTERLENS_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai"
)
LOCAL_URL = os.environ.get("LETTERLENS_LOCAL_URL", "http://127.0.0.1:11434/v1")
LOCAL_MODEL = os.environ.get("LETTERLENS_LOCAL_MODEL", "gemma3:12b")


def configure_console(streams=None, platform=None):
    if not (platform or sys.platform).startswith("win"):
        return
    for stream in streams or (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_env(path=None):
    path = path or os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class KeyPool:
    def __init__(self, keys=None, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clock = clock
        self._next = 0
        self._busy = {}
        self._until = {}
        if keys is None:
            self._keys = self._discover()
        elif isinstance(keys, str):
            self._keys = self._split(keys)
        else:
            self._keys = [key.strip() for key in keys if key and key.strip()]

    @staticmethod
    def _split(value):
        return [part.strip() for part in value.split(",") if part.strip()]

    def _discover(self):
        found = []
        for name in sorted(os.environ):
            upper = name.upper()
            if upper == "LETTERLENS_API_KEY" or ("GEMINI" in upper and "KEY" in upper):
                for part in self._split(os.environ[name]):
                    if part not in found:
                        found.append(part)
        return found

    def acquire(self):
        with self._lock:
            if not self._keys:
                return ""
            count = len(self._keys)
            order = [self._keys[(self._next + step) % count] for step in range(count)]
            now = self._clock()
            ready = [key for key in order if self._until.get(key, 0) <= now]
            candidates = ready or [min(order, key=lambda key: self._until.get(key, 0))]
            key = min(candidates, key=lambda key: self._busy.get(key, 0))
            self._next = (self._keys.index(key) + 1) % count
            self._busy[key] = self._busy.get(key, 0) + 1
            return key

    def release(self, key):
        with self._lock:
            if self._busy.get(key):
                self._busy[key] -= 1

    def cooldown(self, key, seconds):
        with self._lock:
            self._until[key] = max(self._until.get(key, 0), self._clock() + seconds)

    def wait_time(self):
        with self._lock:
            if not self._keys:
                return 0
            now = self._clock()
            return max(0, min(self._until.get(key, 0) for key in self._keys) - now)

    def size(self):
        return len(self._keys)

    def all(self):
        return list(self._keys)
