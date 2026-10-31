import base64
import binascii
import mimetypes
import os
from collections import namedtuple

ALLOWED_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MARKDOWN_EXTS = {".md", ".markdown"}
TEXT_EXTS = {".txt", ".rst", ".csv", ".log", ".json", ".yaml", ".yml", ".html"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024

Image = namedtuple("Image", "mime data")


def parse_data_url(url, max_bytes=MAX_IMAGE_BYTES):
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ValueError("Images must be sent as data URLs")
    head, sep, body = url.partition(",")
    if not sep or not head.endswith(";base64"):
        raise ValueError("Images must be base64 data URLs")
    mime = head[5:-7].lower()
    if mime not in ALLOWED_MIMES:
        raise ValueError(f"Unsupported image type: {mime or 'unknown'}")
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("Image data is not valid base64") from None
    if len(raw) > max_bytes:
        raise ValueError("Image is too large")
    return Image(mime, raw)


def to_data_url(image):
    return f"data:{image.mime};base64,{base64.b64encode(image.data).decode('ascii')}"


def load_image_file(path, max_bytes=MAX_IMAGE_BYTES):
    ext = os.path.splitext(path)[1].lower()
    if ext not in IMAGE_EXTS:
        raise ValueError(f"Unsupported image type: {ext or 'unknown'}")
    mime = mimetypes.types_map.get(ext, "image/png")
    with open(path, "rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Image is too large")
    return Image(mime, data)


def as_image(value):
    if isinstance(value, Image):
        return value
    if isinstance(value, bytes):
        return Image("image/png", value)
    return parse_data_url(value)
