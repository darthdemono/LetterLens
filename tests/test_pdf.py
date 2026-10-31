import base64
import zlib
from pathlib import Path

import pytest

import pdf
from images import Image

DATA = Path(__file__).parent / "data"


def build(objects, catalog=True, extra=b""):
    out = bytearray(b"%PDF-1.7\n")
    for number, body in objects.items():
        if isinstance(body, str):
            body = body.encode("latin-1")
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    if catalog:
        out += b"trailer\n<< /Root 1 0 R >>\n"
    return bytes(out + extra)


def stream(info, body, length=True):
    head = f"<< {info} /Length {len(body)} >>" if length else f"<< {info} >>"
    return head.encode() + b"\nstream\n" + body + b"\nendstream"


def flate(body):
    return zlib.compress(body)


def one_page(content, font="/F1 5 0 R", extra_resources="", pages=None):
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R] /Count 1 /Resources << /Font << " + font + " >> "
           + extra_resources + " >> >>",
        3: "<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        4: stream("", content),
        5: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    objects.update(pages or {})
    return objects


def text_of(content, **kwargs):
    objects = one_page(content, **kwargs)
    doc = pdf.Document(build(objects))
    return pdf.read_page(doc, doc.pages()[0])


def test_real_world_pdfs_are_read():
    for name in ["chromium", "objstm"]:
        result = pdf.extract((DATA / f"{name}.pdf").read_bytes())
        assert "Tisztelt" in result.text and "4 500 Ft-tal nő" in result.text
        assert "2026-11-11" in result.text and "ŐŰ" in result.text or "őű" in result.text
        assert result.images == []
    assert pdf.extract((DATA / "objstm.pdf").read_bytes()).pages == 2
    plain = pdf.extract((DATA / "helvetica.pdf").read_bytes())
    assert plain.text.splitlines()[1].startswith("Határid")


def test_scanned_pdf_returns_the_embedded_jpeg():
    result = pdf.extract((DATA / "scan.pdf").read_bytes())
    assert result.text == "" and len(result.images) == 1
    assert result.images[0].mime == "image/jpeg" and result.images[0].data[:2] == b"\xff\xd8"


def test_not_a_pdf_encrypted_and_garbage():
    with pytest.raises(pdf.PdfError, match="not a PDF"):
        pdf.extract(b"hello")
    with pytest.raises(pdf.PdfError, match="password"):
        pdf.extract(b"%PDF-1.7\ntrailer << /Encrypt 9 0 R >>")
    with pytest.raises(pdf.PdfError, match="could not be read"):
        pdf.extract(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
                    b"2 0 obj\n<< /Type /Pages /Kids 5 >>\nendobj\n"
                    b"trailer << /Root 1 0 R >>")


def test_pdf_without_text_or_pictures():
    with pytest.raises(pdf.PdfError, match="No readable text"):
        pdf.extract(build(one_page(b"0 0 m 10 10 l S")))


def test_short_text_with_pictures_prefers_the_pictures():
    big = flate(bytes(300 * 300))
    objects = one_page(b"BT /F1 12 Tf (Hi) Tj ET /Im0 Do",
                       extra_resources="/XObject << /Im0 6 0 R >>",
                       pages={6: stream("/Type /XObject /Subtype /Image /Width 300 /Height 300 "
                                        "/ColorSpace /DeviceGray /BitsPerComponent 8 "
                                        "/Filter /FlateDecode", big)})
    result = pdf.extract(build(objects))
    assert result.text == "Hi" and result.images[0].mime == "image/png"


def test_long_text_drops_pictures_and_page_limit():
    sentence = b"BT /F1 12 Tf (" + b"word " * 20 + b") Tj ET"
    objects = one_page(sentence)
    result = pdf.extract(build(objects), max_pages=5)
    assert result.images == [] and len(result.text) > 40 and result.pages == 1


def test_parser_strings_names_and_numbers():
    parser = pdf.Parser(rb"(a\(b\)\n\101\0531\
x(nested)) <48656C6C6F2> /Na#20me [1 -2.5 .5 3.] % comment" + b"\n true", 0)
    assert parser.value() == b"a(b)\nA+1x(nested)"
    assert parser.value() == b"Hello "
    assert parser.value() == "Na me"
    assert parser.value() == [1, -2.5, 0.5, 3]
    assert parser.value() == "true"
    assert parser.done()


def test_parser_escapes_and_line_endings():
    assert pdf.Parser(b"(a\\\r\nb\\\rc\\qd)").value() == b"abc" + b"qd"
    assert pdf.Parser(b"(\\5x)").value() == b"\x05x"


def test_parser_references_and_dictionaries():
    value = pdf.Parser(b"<< /A 3 0 R /B [4 0 R 5] /C << /D (x) >> >>").value()
    assert value["A"] == 3 and isinstance(value["A"], pdf.Ref)
    assert value["B"] == [4, 5] and value["C"] == {"D": b"x"}
    plain = pdf.Parser(b"3 0 R", 0, refs=False)
    assert plain.value() == 3


@pytest.mark.parametrize("source", [b"<< 5 6 >>", b"(open", b"<41", b"[1 2", b")"])
def test_parser_rejects_broken_syntax(source):
    with pytest.raises((ValueError, IndexError)):
        parser = pdf.Parser(source)
        parser.value()
        parser.value()


def test_document_stream_lengths_and_recovery():
    body = b"BT ET"
    exact = pdf.Document(build({1: stream("", body)})).objects[1]
    assert exact.raw == body
    wrong = b"<< /Length 99 >>\nstream\r\n" + body + b"\r\nendstream"
    assert pdf.Document(build({1: wrong})).objects[1].raw == body
    indirect = b"<< /Length 2 0 R >>\nstream\n" + body + b"\nendstream"
    assert pdf.Document(build({1: indirect, 2: "5"})).objects[1].raw == body
    carriage = b"<< >>\nstream\r" + body + b"\rendstream"
    assert pdf.Document(build({1: carriage})).objects[1].raw == body
    unfinished = b"<< >>\nstream\n" + body
    assert pdf.Document(build({1: unfinished}, catalog=False)).objects[1].raw.startswith(body)


def test_document_skips_unreadable_objects():
    doc = pdf.Document(b"%PDF-1.4\n1 0 obj\n(unterminated\n2 0 obj\n<< /A 1 >>\nendobj\n")
    assert doc.objects == {2: {"A": 1}}


def test_page_tree_variants():
    objects = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: "<< /Type /Pages /Kids [3 0 R 4 0 R] >>",
        3: "<< /Type /Pages /Kids [5 0 R] /Parent 2 0 R >>",
        4: "<< /Type /Page >>",
        5: "<< /Type /Page /Parent 3 0 R >>",
    }
    assert len(pdf.Document(build(objects)).pages()) == 2
    loose = {1: "<< /Type /Page >>", 2: "<< /Type /Page >>"}
    assert len(pdf.Document(build(loose, catalog=False)).pages()) == 2
    loop = {1: "<< /Type /Catalog /Pages 2 0 R >>", 2: "<< /Type /Pages /Kids [2 0 R] >>"}
    assert pdf.Document(build(loop)).pages() == []
    assert pdf.Document(build({1: "<< /Type /Catalog /Pages 9 0 R >>"})).pages() == []
    doc = pdf.Document(build({1: "<< /Type /Catalog /Pages 2 0 R >>", 2: "5"}))
    assert doc.pages() == []


def test_inherited_resources_and_missing_values():
    doc = pdf.Document(build({1: "<< /Resources << /A 1 >> >>", 2: "<< /Parent 1 0 R >>",
                              3: "<< /Parent 9 0 R >>"}))
    assert doc.inherited(doc.objects[2], "Resources") == {"A": 1}
    assert doc.inherited(doc.objects[3], "Resources") is None
    loop = pdf.Document(build({1: "<< /Parent 1 0 R >>"}))
    assert loop.inherited(loop.objects[1], "Resources") is None


def test_reference_chains_stop():
    doc = pdf.Document(build({1: "2 0 R", 2: "1 0 R"}))
    assert isinstance(doc.get(pdf.Ref(1)), pdf.Ref)
    assert doc.get(7) == 7
    assert doc.info(pdf.Ref(9)) is None


def test_filters():
    doc = pdf.Document(build({}, catalog=False))
    for name, data, expected in [
        ("FlateDecode", flate(b"hello"), b"hello"),
        ("A85", base64.a85encode(b"hello") + b"~>", b"hello"),
        ("ASCII85Decode", base64.a85encode(b"hi"), b"hi"),
        ("ASCIIHexDecode", b"4865 6C6C6F>", b"Hello"),
        ("AHx", b"48656C6C6>", b"Hell`"),
    ]:
        item = pdf.Stream({"Filter": pdf.Name(name)}, data)
        assert doc.decode(item) == expected
    chained = pdf.Stream({"Filter": [pdf.Name("ASCIIHexDecode"), pdf.Name("FlateDecode")]},
                         flate(b"zz").hex().encode() + b">")
    assert doc.decode(chained) == b"zz"
    assert doc.decode(pdf.Stream({}, b"raw")) == b"raw"
    assert doc.decode(pdf.Stream({"Filter": pdf.Name("LZWDecode")}, b"x")) is None
    assert doc.decode(pdf.Stream({"Filter": pdf.Name("FlateDecode")}, b"not zlib")) is None
    assert doc.decode(pdf.Stream({"Filter": pdf.Name("ASCII85Decode")}, b"\x00\x01~>")) is None


def test_object_streams_hold_compressed_objects():
    inner = b"10 0 11 18 << /Type /Page >> (second)"
    objects = {5: stream("/Type /ObjStm /N 2 /First 11 /Filter /FlateDecode", flate(inner))}
    doc = pdf.Document(build(objects))
    assert doc.objects[10] == {"Type": "Page"} and doc.objects[11] == b"second"
    kept = {5: stream("/Type /ObjStm /N 1 /First 5 /Filter /FlateDecode", flate(b"3 0 (x)")),
            3: "(already here)"}
    assert pdf.Document(build(kept)).objects[3] == b"already here"
    broken = {5: stream("/Type /ObjStm /N 1 /First 5 /Filter /LZWDecode", b"xx")}
    assert 3 not in pdf.Document(build(broken)).objects


def test_glyph_names():
    assert [pdf.glyph_char(n) for n in ["odblacute", "Udblacute", "aacute", "uni0151", "A",
                                        "space", "quotedblleft", "bogus", "Xcaron"]] == [
        "ő", "Ű", "á", "ő", "A", " ", "\u201c", "", ""]


def test_cmaps():
    text = """1 begincodespacerange <0000> <FFFF> endcodespacerange
    2 beginbfchar <0003> <0020> <0004> <00410042> endbfchar
    2 beginbfrange <0010> <0012> <0061> <0020> <0021> [<0058> <0059>] endbfrange"""
    mapping, width = pdf.parse_cmap(text)
    assert width == 2 and mapping[3] == " " and mapping[4] == "AB"
    assert [mapping[i] for i in (0x10, 0x11, 0x12)] == ["a", "b", "c"]
    assert mapping[0x20] == "X" and mapping[0x21] == "Y"
    assert pdf.parse_cmap("nothing")[1] == 1
    assert pdf.utf16("") == "�" and pdf.utf16("41") == "䄀"


def font_for(info, objects=None):
    doc = pdf.Document(build(objects or {}, catalog=False))
    return pdf.Font(doc, info)


def test_simple_font_decodes_and_measures():
    font = font_for({"Subtype": "Type1", "FirstChar": 65, "Widths": [600, 700],
                     "Encoding": {"Differences": [65, "odblacute", "bogus", 70, "uni0151"]}})
    assert font.decode(b"ABF") == "őBő"
    assert font.decode(b"AC") == "őC"
    assert font.advance(b"AB") == 1.3 and font.advance(b"Z") == 0.5
    plain = font_for({"Subtype": "Type1", "Encoding": pdf.Name("WinAnsiEncoding")})
    assert plain.decode(b"\xe9\x81") == "é�"


def test_type0_fonts_with_and_without_unicode_maps():
    cmap = flate(b"1 begincodespacerange <0000> <FFFF> endcodespacerange "
                 b"1 beginbfchar <0001> <0048> endbfchar")
    objects = {1: stream("/Filter /FlateDecode", cmap),
               2: "<< /DW 800 /W [1 [500 600] 5 7 300] >>"}
    font = font_for({"Subtype": "Type0", "ToUnicode": pdf.Ref(1),
                     "DescendantFonts": [pdf.Ref(2)]}, objects)
    assert font.decode(b"\x00\x01\x00\x09") == "H" and font.width == 2
    assert font.advance(b"\x00\x01\x00\x06\x00\x09") == (500 + 300 + 800) / 1000
    blind = font_for({"Subtype": "Type0"})
    assert blind.decode(b"\x00\x01") == "" and blind.advance(b"\x00\x01") == 1.0
    bad_map = {1: stream("/Filter /LZWDecode", b"x")}
    assert font_for({"Subtype": "Type1", "ToUnicode": pdf.Ref(1)}, bad_map).decode(b"A") == "A"
    one_byte = flate(b"1 begincodespacerange <00> <FF> endcodespacerange "
                     b"1 beginbfchar <41> <0058> endbfchar")
    simple = font_for({"Subtype": "TrueType", "ToUnicode": pdf.Ref(1)},
                      {1: stream("/Filter /FlateDecode", one_byte)})
    assert simple.decode(b"AB") == "X" and simple.width == 1


def test_text_operators():
    text, images = text_of(b"BT /F1 10 Tf (Hello) Tj (World) Tj ET")
    assert text == "HelloWorld" and images == []
    text, _ = text_of(b"BT /F1 10 Tf 1 0 0 1 10 700 Tm (A) Tj 1 0 0 1 10 680 Tm (B) Tj "
                      b"1 0 0 1 60 680 Tm (C) Tj ET")
    assert text == "A\nB C"
    text, _ = text_of(b"BT /F1 10 Tf 0 0 Td (A) Tj 30 0 Td (B) Tj 0 -12 TD (C) Tj T* (D) Tj "
                      b"(E) ' 1 2 (F) \" ET")
    assert text == "A B\nC\nD\nE\nF"
    text, _ = text_of(b"BT /F1 10 Tf [(A) -50 (B) -400 (C) 20] TJ ET")
    assert text == "AB C"
    text, _ = text_of(b"BT /F1 10 Tf 5 0 Td (tight) Tj 0 0 Td (x) Tj ET")
    assert text == "tightx"


def test_operators_without_operands_or_font_are_ignored():
    text, _ = text_of(b"BT (no font) Tj /Missing 9 Tf (x) Tj [(y)] TJ Tj TJ Tf Td Tm Do ET")
    assert text == ""


def test_inline_images_are_skipped():
    text, _ = text_of(b"BT /F1 10 Tf (A) Tj ET BI /W 1 ID \x00\xff(binary) EI BT (B) Tj ET")
    assert text == "AB"
    text, _ = text_of(b"BT /F1 10 Tf (A) Tj ET BI /W 1 ID unfinished")
    assert text == "A"


def test_form_xobjects_are_followed_with_a_depth_limit():
    form = stream("/Type /XObject /Subtype /Form /Resources << /Font << /F1 5 0 R >> "
                  "/XObject << /Fm0 6 0 R >> >>",
                  b"BT /F1 10 Tf (inside) Tj ET /Fm0 Do")
    objects = one_page(b"/Fm0 Do /Fm1 Do /Missing Do /Plain Do",
                       extra_resources="/XObject << /Fm0 6 0 R /Fm1 7 0 R /Plain 8 0 R >>",
                       pages={6: form,
                              7: stream("/Subtype /Form", b"BT /F1 10 Tf (shared) Tj ET"),
                              8: stream("/Subtype /PS", b"x")})
    doc = pdf.Document(build(objects))
    text, _ = pdf.read_page(doc, doc.pages()[0])
    assert text.count("inside") == 3 and "shared" in text
    broken = pdf.Document(build(one_page(
        b"/Fm0 Do", extra_resources="/XObject << /Fm0 6 0 R >>",
        pages={6: stream("/Subtype /Form /Filter /LZWDecode", b"x")})))
    assert pdf.read_page(broken, broken.pages()[0])[0] == ""


def test_contents_arrays_and_missing_contents():
    objects = one_page(b"")
    objects[3] = "<< /Type /Page /Parent 2 0 R /Contents [4 0 R 6 0 R 9 0 R 7 0 R] >>"
    objects[6] = stream("", b"BT /F1 10 Tf (one) Tj ET")
    objects[7] = stream("/Filter /LZWDecode", b"x")
    doc = pdf.Document(build(objects))
    assert pdf.read_page(doc, doc.pages()[0])[0] == "one"
    objects[3] = "<< /Type /Page /Parent 2 0 R >>"
    doc = pdf.Document(build(objects))
    assert pdf.read_page(doc, doc.pages()[0])[0] == ""


def test_fonts_are_cached_and_missing_fonts_ignored():
    text, _ = text_of(b"BT /F1 10 Tf (a) Tj /F1 10 Tf (b) Tj /Nope 10 Tf (c) Tj ET")
    assert text == "ab"


def image_stream(width, height, extra, body, filt="/FlateDecode"):
    info = (f"/Type /XObject /Subtype /Image /Width {width} /Height {height} {extra} "
            f"/Filter {filt}")
    return stream(info, body)


def image_of(stream_bytes, extra_objects=None):
    objects = {1: stream_bytes, **(extra_objects or {})}
    doc = pdf.Document(build(objects, catalog=False))
    return pdf.to_image(doc, doc.objects[1])


def test_raw_flate_images_become_pngs():
    gray = image_of(image_stream(300, 2, "/ColorSpace /DeviceGray /BitsPerComponent 8",
                                 flate(bytes(600))))
    assert gray.mime == "image/png" and gray.data.startswith(b"\x89PNG")
    rgb = image_of(image_stream(300, 1, "/ColorSpace /DeviceRGB /BitsPerComponent 8",
                                flate(bytes(900))))
    assert rgb.data[25] == 2
    icc = image_of(image_stream(300, 1, "/ColorSpace [/ICCBased 2 0 R] /BitsPerComponent 8",
                                flate(bytes(300))),
                   {2: "<< /N 1 >>"})
    assert icc.data[25] == 0
    rows = b"".join(b"\x00" + bytes(300) for _ in range(2))
    predicted = image_of(image_stream(
        300, 2, "/ColorSpace /DeviceGray /BitsPerComponent 8 "
                "/DecodeParms [<< /Predictor 15 /Columns 300 /Colors 1 >>]", flate(rows)))
    assert predicted.mime == "image/png"


@pytest.mark.parametrize(
    "extra, body",
    [
        ("/ColorSpace /DeviceCMYK /BitsPerComponent 8", bytes(1200)),
        ("/ColorSpace /DeviceGray /BitsPerComponent 1", bytes(600)),
        ("/ColorSpace /DeviceGray /BitsPerComponent 8", bytes(10)),
        ("/ColorSpace /DeviceGray /BitsPerComponent 8 /DecodeParms << /Predictor 2 >>", bytes(600)),
        ("/ColorSpace /DeviceGray /BitsPerComponent 8 /DecodeParms << /Predictor 15 "
         "/Columns 5 /Colors 1 >>", bytes(602)),
        ("/ColorSpace /DeviceGray /BitsPerComponent 8 /DecodeParms << /Predictor 15 "
         "/Columns 300 /Colors 1 >>", bytes(5)),
    ],
)
def test_unsupported_flate_images_are_skipped(extra, body):
    assert image_of(image_stream(300, 2, extra, flate(body))) is None


def test_other_image_cases():
    jpeg = image_of(image_stream(400, 400, "/ColorSpace /DeviceRGB", b"\xff\xd8data",
                                 filt="/DCTDecode"))
    assert jpeg == Image("image/jpeg", b"\xff\xd8data")
    assert image_of(image_stream(100, 100, "", b"x", filt="/DCTDecode")) is None
    assert image_of(image_stream(400, 400, "", b"x", filt="/JPXDecode")) is None
    assert image_of(stream("/Subtype /Image /Width (x) /Height 400", b"x")) is None
    corrupt = image_of(image_stream(300, 2, "/ColorSpace /DeviceGray /BitsPerComponent 8",
                                    b"not zlib"))
    assert corrupt is None
    assert image_of(image_stream(300, 2, "/ColorSpace /DeviceGray /BitsPerComponent 8",
                                 flate(bytes(600)), filt="/FlateDecode")) is not None


def test_oversized_images_are_skipped(monkeypatch):
    monkeypatch.setattr(pdf, "MAX_IMAGE_BYTES", 10)
    assert image_of(image_stream(400, 400, "", b"\xff\xd8" + bytes(50), filt="/DCTDecode")) is None


def test_tidy_cleans_whitespace():
    assert pdf.tidy("a  \t b \n\n\n\n c \n d ") == "a b\n\nc\nd"


def test_stream_edge_syntax():
    glued = b"<< >>\nstream" + b"BT ET" + b"endstream"
    assert pdf.Document(build({1: glued})).objects[1].raw == b"BT ET"
    tight = b"<< /Length 99 >>\nstream\nBT ET" + b"endstream"
    assert pdf.Document(build({1: tight})).objects[1].raw == b"BT ET"


def test_odd_values_inside_arrays_and_small_images_are_ignored():
    text, images = text_of(b"BT /F1 10 Tf [(A) /Odd (B)] TJ ET /Im0 Do",
                           extra_resources="/XObject << /Im0 6 0 R >>",
                           pages={6: image_stream(10, 10, "/ColorSpace /DeviceGray "
                                                  "/BitsPerComponent 8", flate(bytes(100)))})
    assert text == "AB" and images == []
