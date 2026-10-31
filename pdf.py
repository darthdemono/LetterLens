import base64
import re
import struct
import unicodedata
import zlib
from collections import namedtuple

from images import MAX_IMAGE_BYTES, Image

Pdf = namedtuple("Pdf", "text images pages")

MAX_PDF_BYTES = 20 * 1024 * 1024
MIN_TEXT_CHARS = 40
MIN_IMAGE_SIDE = 300
MAX_PAGES = 20
MAX_IMAGES = 4
MAX_FORM_DEPTH = 3
WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"
REFERENCE = re.compile(rb"(\d+)\s+(\d+)\s+R(?![A-Za-z0-9])")
OBJECT = re.compile(rb"(?<!\d)(\d+)\s+(\d+)\s+obj\b")
NUMBER = re.compile(rb"[+-]?(\d+\.?\d*|\.\d+)$")
ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\x0c"}
ACCENTS = {
    "acute": "ACUTE", "grave": "GRAVE", "dieresis": "DIAERESIS", "circumflex": "CIRCUMFLEX",
    "tilde": "TILDE", "dblacute": "DOUBLE ACUTE", "caron": "CARON", "ring": "RING ABOVE",
    "cedilla": "CEDILLA", "ogonek": "OGONEK", "macron": "MACRON", "breve": "BREVE",
    "dotaccent": "DOT ABOVE",
}
GLYPHS = {
    "space": " ", "hyphen": "-", "endash": "–", "emdash": "—", "bullet": "•",
    "quoteleft": "‘", "quoteright": "’", "quotedblleft": "“",
    "quotedblright": "”", "fi": "fi", "fl": "fl", "Euro": "€", "germandbls": "ß",
}
GLYPH_ACCENT = re.compile(r"^([A-Za-z])(" + "|".join(ACCENTS) + ")$")
GLYPH_UNI = re.compile(r"^uni([0-9A-Fa-f]{4})$")


class PdfError(ValueError):
    pass


class Name(str):
    pass


class Op(str):
    pass


class Ref(int):
    pass


class Stream:
    def __init__(self, info, raw):
        self.info = info
        self.raw = raw


class Parser:
    def __init__(self, data, pos=0, refs=True):
        self.data = data
        self.pos = pos
        self.refs = refs

    def skip(self):
        while self.pos < len(self.data):
            char = self.data[self.pos:self.pos + 1]
            if char in WHITESPACE:
                self.pos += 1
            elif char == b"%":
                end = re.compile(rb"[\r\n]").search(self.data, self.pos)
                self.pos = end.start() if end else len(self.data)
            else:
                return

    def done(self):
        self.skip()
        return self.pos >= len(self.data)

    def value(self):
        self.skip()
        char = self.data[self.pos:self.pos + 1]
        if char == b"<":
            if self.data[self.pos + 1:self.pos + 2] == b"<":
                return self.mapping()
            return self.hex_string()
        if char == b"(":
            return self.literal()
        if char == b"[":
            return self.array()
        if char == b"/":
            return self.name()
        return self.token()

    def mapping(self):
        self.pos += 2
        result = {}
        while True:
            self.skip()
            if self.data[self.pos:self.pos + 2] == b">>":
                self.pos += 2
                return result
            key = self.value()
            if not isinstance(key, Name):
                raise ValueError("bad dictionary key")
            result[str(key)] = self.value()

    def array(self):
        self.pos += 1
        result = []
        while True:
            self.skip()
            if self.data[self.pos:self.pos + 1] == b"]":
                self.pos += 1
                return result
            result.append(self.value())

    def name(self):
        self.pos += 1
        start = self.pos
        while self.pos < len(self.data) and self.data[self.pos:self.pos + 1] not in (
                WHITESPACE + DELIMITERS):
            self.pos += 1
        raw = self.data[start:self.pos]
        raw = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), raw)
        return Name(raw.decode("latin-1"))

    def hex_string(self):
        end = self.data.index(b">", self.pos)
        digits = re.sub(rb"\s", b"", self.data[self.pos + 1:end])
        self.pos = end + 1
        if len(digits) % 2:
            digits += b"0"
        return bytes.fromhex(digits.decode("ascii"))

    def literal(self):
        self.pos += 1
        depth = 1
        out = bytearray()
        while True:
            char = self.data[self.pos:self.pos + 1]
            if not char:
                raise ValueError("unterminated string")
            self.pos += 1
            if char == b"\\":
                out += self.escape()
            elif char == b"(":
                depth += 1
                out += char
            elif char == b")":
                depth -= 1
                if depth == 0:
                    return bytes(out)
                out += char
            else:
                out += char

    def escape(self):
        char = self.data[self.pos:self.pos + 1]
        self.pos += 1
        if char in ESCAPES:
            return ESCAPES[char]
        if char in b"01234567" and char:
            digits = char
            while len(digits) < 3 and self.data[self.pos:self.pos + 1] in tuple(
                    bytes([c]) for c in b"01234567"):
                digits += self.data[self.pos:self.pos + 1]
                self.pos += 1
            return bytes([int(digits, 8) & 255])
        if char == b"\r":
            if self.data[self.pos:self.pos + 1] == b"\n":
                self.pos += 1
            return b""
        if char == b"\n":
            return b""
        return char

    def token(self):
        if self.refs:
            found = REFERENCE.match(self.data, self.pos)
            if found:
                self.pos = found.end()
                return Ref(int(found.group(1)))
        start = self.pos
        while self.pos < len(self.data) and self.data[self.pos:self.pos + 1] not in (
                WHITESPACE + DELIMITERS):
            self.pos += 1
        if self.pos == start:
            raise ValueError("unexpected character")
        word = self.data[start:self.pos]
        if NUMBER.match(word):
            number = float(word)
            return int(number) if number.is_integer() and b"." not in word else number
        return Op(word.decode("latin-1"))


class Document:
    def __init__(self, data):
        self.data = data
        self.objects = {}
        self.scan()
        self.unpack_object_streams()

    def scan(self):
        pos = 0
        while True:
            found = OBJECT.search(self.data, pos)
            if not found:
                return
            parser = Parser(self.data, found.end())
            try:
                value = parser.value()
            except (ValueError, IndexError):
                pos = found.end()
                continue
            pos = parser.pos
            parser.skip()
            if isinstance(value, dict) and self.data.startswith(b"stream", parser.pos):
                value, pos = self.read_stream(value, parser.pos + 6)
            self.objects[int(found.group(1))] = value

    def read_stream(self, info, start):
        if self.data[start:start + 2] == b"\r\n":
            start += 2
        elif self.data[start:start + 1] in (b"\n", b"\r"):
            start += 1
        length = info.get("Length")
        if isinstance(length, int) and self.data[start + length:start + length + 20].lstrip(
                WHITESPACE).startswith(b"endstream"):
            end = start + length
        else:
            end = self.data.find(b"endstream", start)
            end = len(self.data) if end < 0 else end
            if self.data[end - 2:end] == b"\r\n":
                end -= 2
            elif self.data[end - 1:end] in (b"\n", b"\r"):
                end -= 1
        return Stream(info, self.data[start:end]), end

    def unpack_object_streams(self):
        for item in list(self.objects.values()):
            if not (isinstance(item, Stream) and item.info.get("Type") == "ObjStm"):
                continue
            body = self.decode(item)
            if body is None:
                continue
            count = self.get(item.info.get("N"))
            first = self.get(item.info.get("First"))
            head = Parser(body, 0, refs=False)
            pairs = [(head.value(), head.value()) for _ in range(count)]
            for number, offset in pairs:
                if number not in self.objects:
                    self.objects[number] = Parser(body, first + offset).value()

    def get(self, value):
        seen = 0
        while isinstance(value, Ref) and seen < 20:
            value = self.objects.get(int(value))
            seen += 1
        return value

    def info(self, value):
        value = self.get(value)
        return value.info if isinstance(value, Stream) else value

    def decode(self, stream):
        filters = self.get(stream.info.get("Filter")) or []
        filters = [self.get(item) for item in (filters if isinstance(filters, list) else [filters])]
        data = stream.raw
        for name in filters:
            try:
                data = self.apply(name, data)
            except (zlib.error, ValueError):
                return None
            if data is None:
                return None
        return data

    @staticmethod
    def apply(name, data):
        if name in ("FlateDecode", "Fl"):
            return zlib.decompressobj().decompress(data)
        if name in ("ASCII85Decode", "A85"):
            text = data.strip()
            return base64.a85decode(text[:-2] if text.endswith(b"~>") else text)
        if name in ("ASCIIHexDecode", "AHx"):
            digits = re.sub(rb"\s", b"", data.split(b">")[0])
            return bytes.fromhex((digits + (b"0" if len(digits) % 2 else b"")).decode("ascii"))
        return None

    def pages(self):
        catalog = next((v for v in self.objects.values()
                        if isinstance(v, dict) and v.get("Type") == "Catalog"), None)
        root = self.get(catalog.get("Pages")) if catalog else None
        found = []
        if root:
            self.walk(root, found, 0)
        if not found:
            found = [v for v in self.objects.values()
                     if isinstance(v, dict) and v.get("Type") == "Page"]
        return found

    def walk(self, node, found, depth):
        node = self.get(node)
        if not isinstance(node, dict) or depth > 30:
            return
        if node.get("Type") == "Page" or "Kids" not in node:
            found.append(node)
            return
        for kid in self.get(node["Kids"]):
            self.walk(kid, found, depth + 1)

    def inherited(self, page, key):
        node = page
        for _ in range(30):
            if key in node:
                return self.get(node[key])
            node = self.get(node.get("Parent"))
            if not isinstance(node, dict):
                return None
        return None


def glyph_char(name):
    if name in GLYPHS:
        return GLYPHS[name]
    if len(name) == 1:
        return name
    uni = GLYPH_UNI.match(name)
    if uni:
        return chr(int(uni.group(1), 16))
    accent = GLYPH_ACCENT.match(name)
    if accent:
        base, mark = accent.groups()
        kind = "CAPITAL" if base.isupper() else "SMALL"
        try:
            return unicodedata.lookup(
                f"LATIN {kind} LETTER {base.upper()} WITH {ACCENTS[mark]}")
        except KeyError:
            return ""
    return ""


def parse_cmap(text):
    mapping = {}
    width = 1
    space = re.search(r"begincodespacerange\s*<([0-9A-Fa-f]+)>", text)
    if space:
        width = max(1, len(space.group(1)) // 2)
    for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
        for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", block):
            mapping[int(source, 16)] = utf16(target)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
        for low, high, rest in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]*>)", block):
            start, stop = int(low, 16), int(high, 16)
            if rest.startswith("["):
                targets = re.findall(r"<([0-9A-Fa-f]*)>", rest)
                for offset, target in enumerate(targets[:stop - start + 1]):
                    mapping[start + offset] = utf16(target)
            else:
                base = utf16(rest.strip("<>"))
                for offset in range(min(stop - start + 1, 65536)):
                    mapping[start + offset] = base[:-1] + chr(ord(base[-1]) + offset)
    return mapping, width


def utf16(digits):
    if len(digits) % 4:
        digits += "0" * (4 - len(digits) % 4)
    return bytes.fromhex(digits).decode("utf-16-be", "replace") or "�"


class Font:
    def __init__(self, doc, info):
        self.mapping = None
        self.width = 1
        self.names = {}
        self.readable = True
        self.default = 500
        self.widths = {}
        self.read_widths(doc, info)
        cmap = doc.get(info.get("ToUnicode"))
        if isinstance(cmap, Stream):
            body = doc.decode(cmap)
            if body is not None:
                self.mapping, cmap_width = parse_cmap(body.decode("latin-1"))
                self.width = cmap_width if info.get("Subtype") == "Type0" else 1
        if self.mapping is None:
            if info.get("Subtype") == "Type0":
                self.readable = False
            else:
                self.read_differences(doc, doc.get(info.get("Encoding")))

    def read_widths(self, doc, info):
        if info.get("Subtype") != "Type0":
            first = doc.get(info.get("FirstChar")) or 0
            for index, value in enumerate(doc.get(info.get("Widths")) or []):
                self.widths[first + index] = doc.get(value)
            return
        self.width = 2
        descendants = doc.get(info.get("DescendantFonts")) or [{}]
        inner = doc.get(descendants[0]) or {}
        self.default = doc.get(inner.get("DW")) or 1000
        entries = [doc.get(item) for item in doc.get(inner.get("W")) or []]
        index = 0
        while index < len(entries) - 1:
            start, rest = entries[index], entries[index + 1]
            if isinstance(rest, list):
                for offset, value in enumerate(rest):
                    self.widths[start + offset] = doc.get(value)
                index += 2
            else:
                for code in range(start, min(rest, start + 65535) + 1):
                    self.widths[code] = entries[index + 2]
                index += 3

    def advance(self, raw):
        step = self.width
        codes = [int.from_bytes(raw[i:i + step], "big") for i in range(0, len(raw), step)]
        return sum(self.widths.get(code, self.default) for code in codes) / 1000

    def read_differences(self, doc, encoding):
        if not isinstance(encoding, dict):
            return
        code = 0
        for item in doc.get(encoding.get("Differences")) or []:
            if isinstance(item, int):
                code = item
            else:
                self.names[code] = glyph_char(str(item))
                code += 1

    def decode(self, raw):
        if not self.readable:
            return ""
        if self.mapping is not None:
            codes = [int.from_bytes(raw[i:i + self.width], "big")
                     for i in range(0, len(raw), self.width)]
            return "".join(self.mapping.get(code, "") for code in codes)
        return "".join(self.names.get(b) or bytes([b]).decode("cp1252", "replace") for b in raw)


class Page:
    def __init__(self, doc):
        self.doc = doc
        self.text = []
        self.images = []
        self.fonts = {}

    def font(self, resources, name):
        table = self.doc.get(resources.get("Font")) or {}
        key = (id(resources), name)
        if key not in self.fonts:
            info = self.doc.get(table.get(name))
            self.fonts[key] = Font(self.doc, info) if isinstance(info, dict) else None
        return self.fonts[key]

    def run(self, data, resources, depth=0):
        state = {"font": None, "size": 12.0, "x": 0.0, "line": 0.0, "y": None}
        operands = []
        parser = Parser(data, 0, refs=False)
        while not parser.done():
            item = parser.value()
            if not isinstance(item, Op):
                operands.append(item)
                continue
            op = str(item)
            if op == "BI":
                end = re.compile(rb"\sEI(?![A-Za-z0-9])").search(data, parser.pos)
                parser.pos = end.end() if end else len(data)
            elif op == "Tf" and len(operands) >= 2:
                state["font"] = self.font(resources, operands[0])
                state["size"] = abs(float(operands[1])) or 12.0
            elif op in ("Tj", "'", '"') and operands:
                if op != "Tj":
                    self.newline()
                self.show(state, operands[-1])
            elif op == "TJ" and operands and isinstance(operands[-1], list):
                for part in operands[-1]:
                    if isinstance(part, bytes):
                        self.show(state, part)
                    elif isinstance(part, (int, float)):
                        state["x"] -= part / 1000 * state["size"]
                        if part < -200:
                            self.text.append(" ")
            elif op in ("Td", "TD") and len(operands) >= 2:
                self.move(state, state["line"] + operands[0], (state["y"] or 0) + operands[1],
                          operands[1] != 0)
            elif op == "T*":
                self.newline()
            elif op == "Tm" and len(operands) >= 6:
                last = state["y"]
                self.move(state, operands[4], operands[5],
                          last is not None and abs(operands[5] - last) > 1)
            elif op == "Do" and operands:
                self.xobject(resources, operands[0], depth)
            operands = []

    def move(self, state, x, y, newline):
        if newline:
            self.newline()
        elif self.text and self.text[-1] != "\n" and x - state["x"] > 0.2 * state["size"]:
            self.text.append(" ")
        state["line"] = state["x"] = x
        state["y"] = y

    def newline(self):
        if self.text and self.text[-1] != "\n":
            self.text.append("\n")

    def show(self, state, raw):
        font = state["font"]
        if font is not None and isinstance(raw, bytes):
            self.text.append(font.decode(raw))
            state["x"] += font.advance(raw) * state["size"]

    def xobject(self, resources, name, depth):
        table = self.doc.get(resources.get("XObject")) or {}
        item = self.doc.get(table.get(name))
        if not isinstance(item, Stream):
            return
        if item.info.get("Subtype") == "Form":
            inner = self.doc.info(item.info.get("Resources")) or resources
            body = self.doc.decode(item)
            if body is not None and depth < MAX_FORM_DEPTH:
                self.run(body, inner, depth + 1)
        elif item.info.get("Subtype") == "Image":
            picture = to_image(self.doc, item)
            if picture:
                self.images.append(picture)


def png_chunk(kind, body):
    packed = kind + body
    return struct.pack(">I", len(body)) + packed + struct.pack(">I", zlib.crc32(packed))


def to_image(doc, stream):
    info = stream.info
    width, height = doc.get(info.get("Width")), doc.get(info.get("Height"))
    if not (isinstance(width, int) and isinstance(height, int)):
        return None
    if max(width, height) < MIN_IMAGE_SIDE:
        return None
    filters = doc.get(info.get("Filter")) or []
    filters = [doc.get(i) for i in (filters if isinstance(filters, list) else [filters])]
    if filters == ["DCTDecode"]:
        picture = Image("image/jpeg", stream.raw)
    elif filters == ["FlateDecode"]:
        picture = flate_image(doc, stream, width, height)
    else:
        return None
    if picture is None or len(picture.data) > MAX_IMAGE_BYTES:
        return None
    return picture


def channels(doc, space):
    space = doc.get(space)
    if isinstance(space, list) and space and space[0] == "ICCBased":
        profile = doc.info(space[1]) or {}
        return {1: (0, 1), 3: (2, 3)}.get(doc.get(profile.get("N")))
    return {"DeviceGray": (0, 1), "DeviceRGB": (2, 3)}.get(space)


def flate_image(doc, stream, width, height):
    kind = channels(doc, stream.info.get("ColorSpace"))
    if kind is None or doc.get(stream.info.get("BitsPerComponent")) != 8:
        return None
    colour, count = kind
    body = doc.decode(stream)
    if body is None:
        return None
    parms = doc.get(stream.info.get("DecodeParms"))
    parms = doc.get(parms[0]) if isinstance(parms, list) and parms else parms
    predictor = (doc.get(parms.get("Predictor")) if isinstance(parms, dict) else None) or 1
    stride = width * count
    if predictor >= 10:
        columns = doc.get(parms.get("Columns")) or 1
        colors = doc.get(parms.get("Colors")) or 1
        if columns != width or colors != count or len(body) < height * (stride + 1):
            return None
        rows = body[:height * (stride + 1)]
    elif predictor == 1 and len(body) >= height * stride:
        rows = b"".join(b"\x00" + body[r * stride:(r + 1) * stride] for r in range(height))
    else:
        return None
    header = struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
            + png_chunk(b"IDAT", zlib.compress(rows)) + png_chunk(b"IEND", b""))
    return Image("image/png", data)


def read_page(doc, node):
    page = Page(doc)
    resources = doc.inherited(node, "Resources") or {}
    contents = doc.get(node.get("Contents"))
    parts = contents if isinstance(contents, list) else [contents]
    chunks = []
    for part in parts:
        part = doc.get(part)
        body = doc.decode(part) if isinstance(part, Stream) else None
        if body is not None:
            chunks.append(body)
    page.run(b"\n".join(chunks), resources)
    return "".join(page.text), page.images


def tidy(text):
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract(data, max_pages=MAX_PAGES, max_images=MAX_IMAGES):
    if b"%PDF" not in data[:1024]:
        raise PdfError("This file is not a PDF.")
    if re.search(rb"/Encrypt\b", data):
        raise PdfError("This PDF is password protected. Remove the password and try again.")
    try:
        doc = Document(data)
        nodes = doc.pages()
        texts = []
        images = []
        for node in nodes[:max_pages]:
            text, pictures = read_page(doc, node)
            texts.append(tidy(text))
            images += pictures
    except (ValueError, IndexError, KeyError, TypeError, AttributeError, RecursionError):
        raise PdfError("This PDF could not be read. Export the pages as images instead.") from None
    text = "\n\n".join(item for item in texts if item)
    if len(text) >= MIN_TEXT_CHARS:
        images = []
    else:
        images = images[:max_images]
    if not text and not images:
        raise PdfError("No readable text or pictures were found in this PDF. "
                       "Export the pages as images instead.")
    return Pdf(text, images, len(nodes))


def inputs(data, name):
    result = extract(data)
    files = [{"name": name, "content": result.text}] if result.text else []
    return files, list(result.images)
