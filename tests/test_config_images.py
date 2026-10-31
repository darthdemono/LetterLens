import base64
import io
import os

import pytest

import config
import images


class Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_load_env_reads_file_without_overriding(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('# comment\n\nA_ONE="x y"\nB_TWO=\'q\'\nbroken line\nKEEP=new\n', encoding="utf-8")
    monkeypatch.setenv("KEEP", "old")
    monkeypatch.delenv("A_ONE", raising=False)
    monkeypatch.delenv("B_TWO", raising=False)
    config.load_env(str(env))
    assert os.environ["A_ONE"] == "x y"
    assert os.environ["B_TWO"] == "q"
    assert os.environ["KEEP"] == "old"
    monkeypatch.delenv("A_ONE")
    monkeypatch.delenv("B_TWO")


def test_load_env_missing_file_is_fine(tmp_path):
    config.load_env(str(tmp_path / "nope.env"))


def test_load_env_default_path_is_project_env(monkeypatch):
    monkeypatch.setattr(config.os.path, "isfile", lambda path: False)
    config.load_env()


def test_configure_console_only_on_windows():
    quiet, loud = Stream(), Stream()
    config.configure_console([quiet], platform="linux")
    assert quiet.calls == []
    config.configure_console([loud, io.StringIO()], platform="win32")
    assert loud.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_configure_console_defaults_to_real_streams():
    config.configure_console(platform="win32")


def test_key_pool_from_string_list_and_round_robin():
    assert config.KeyPool("a, b,,c").all() == ["a", "b", "c"]
    pool = config.KeyPool([" a ", "", None, "b"])
    assert pool.size() == 2
    keys = []
    for _ in range(3):
        key = pool.acquire()
        pool.release(key)
        keys.append(key)
    assert keys == ["a", "b", "a"]


def test_key_pool_empty_returns_blank_key():
    pool = config.KeyPool([])
    assert pool.size() == 0
    assert pool.acquire() == ""
    assert pool.wait_time() == 0


def test_key_pool_discovers_env_keys(monkeypatch):
    monkeypatch.setenv("GEMINI_1_KEY", "a,b")
    monkeypatch.setenv("GEMINI_2_KEY", "b, c")
    monkeypatch.setenv("LETTERLENS_API_KEY", "d")
    monkeypatch.setenv("OTHER_KEY", "nope")
    assert config.KeyPool().all() == ["a", "b", "c", "d"]


PNG = base64.b64encode(b"\x89PNG-bytes").decode()


def test_parse_data_url_accepts_png():
    image = images.parse_data_url(f"data:image/PNG;base64,{PNG}")
    assert image == images.Image("image/png", b"\x89PNG-bytes")
    assert images.to_data_url(image) == f"data:image/png;base64,{PNG}"


@pytest.mark.parametrize(
    "value, message",
    [
        (None, "data URLs"),
        ("/etc/passwd", "data URLs"),
        ("data:image/png;base64", "base64 data URLs"),
        ("data:image/png,abc", "base64 data URLs"),
        (f"data:text/html;base64,{PNG}", "Unsupported image type"),
        ("data:;base64,AAAA", "Unsupported image type: unknown"),
        ("data:image/png;base64,@@@", "not valid base64"),
    ],
)
def test_parse_data_url_rejects_bad_input(value, message):
    with pytest.raises(ValueError, match=message):
        images.parse_data_url(value)


def test_parse_data_url_size_limit():
    with pytest.raises(ValueError, match="too large"):
        images.parse_data_url(f"data:image/png;base64,{PNG}", max_bytes=3)


def test_load_image_file(tmp_path):
    good = tmp_path / "scan.JPG"
    good.write_bytes(b"jpegdata")
    assert images.load_image_file(str(good)) == images.Image("image/jpeg", b"jpegdata")
    with pytest.raises(ValueError, match="Unsupported image type: .txt"):
        images.load_image_file(str(tmp_path / "a.txt"))
    with pytest.raises(ValueError, match="unknown"):
        images.load_image_file(str(tmp_path / "noext"))
    with pytest.raises(ValueError, match="too large"):
        images.load_image_file(str(good), max_bytes=2)


def test_as_image_accepts_three_forms():
    image = images.Image("image/webp", b"x")
    assert images.as_image(image) is image
    assert images.as_image(b"raw") == images.Image("image/png", b"raw")
    assert images.as_image(f"data:image/gif;base64,{PNG}").mime == "image/gif"
    with pytest.raises(ValueError):
        images.as_image("/some/path.png")


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def test_key_pool_prefers_the_least_busy_key():
    pool = config.KeyPool(["a", "b"])
    first = pool.acquire()
    second = pool.acquire()
    third = pool.acquire()
    assert {first, second} == {"a", "b"}
    pool.release(first)
    pool.release(first)
    pool.release("unknown")
    assert pool.acquire() == first
    assert third in {"a", "b"}


def test_key_pool_cooldown_and_recovery():
    clock = Clock()
    pool = config.KeyPool(["a", "b"], clock=clock)
    pool.cooldown("a", 30)
    pool.cooldown("a", 10)
    assert pool.wait_time() == 0
    for _ in range(3):
        key = pool.acquire()
        pool.release(key)
        assert key == "b"
    clock.now += 31
    assert {pool.acquire(), pool.acquire()} == {"a", "b"}


def test_key_pool_when_everything_is_cooling_down_picks_the_soonest():
    clock = Clock()
    pool = config.KeyPool(["a", "b"], clock=clock)
    pool.cooldown("a", 50)
    pool.cooldown("b", 20)
    assert pool.wait_time() == 20
    assert pool.acquire() == "b"
