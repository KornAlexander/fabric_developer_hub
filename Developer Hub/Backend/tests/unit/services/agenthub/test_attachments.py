"""Unit tests for ``services.agenthub.attachments``."""
from __future__ import annotations

import base64
import io
import struct
import zlib

import pytest

from services.agenthub.attachments import (
    MAX_BYTES_PER_FILE,
    MAX_PDF_TEXT_CHARS,
    MAX_TOTAL_BYTES,
    _decode_data_uri,
    _extract_pdf_text,
    process_attachments,
)


# ─────────────────────────────────────────────────────────────
# _decode_data_uri
# ─────────────────────────────────────────────────────────────
def test_decode_data_uri_png() -> None:
    raw = b"\x89PNG\r\n\x1a\n"
    uri = "data:image/png;base64," + base64.b64encode(raw).decode()
    mime, out = _decode_data_uri(uri)
    assert mime == "image/png"
    assert out == raw


def test_decode_data_uri_defaults_mime_when_missing() -> None:
    uri = "data:;base64," + base64.b64encode(b"hi").decode()
    mime, _ = _decode_data_uri(uri)
    assert mime == "application/octet-stream"


def test_decode_data_uri_rejects_non_data() -> None:
    with pytest.raises(ValueError, match="data: URI"):
        _decode_data_uri("http://example.com/x.png")


def test_decode_data_uri_rejects_non_base64() -> None:
    with pytest.raises(ValueError, match="base64-encoded"):
        _decode_data_uri("data:text/plain,hello")


# ─────────────────────────────────────────────────────────────
# _extract_pdf_text
# ─────────────────────────────────────────────────────────────
def _minimal_pdf_with_text(text: str) -> bytes:
    """Return a minimal valid single-page PDF whose content stream holds ``text``."""
    # Hand-crafted tiny PDF; pypdf's extract_text can read the Tj operator.
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    stream = zlib.compress(content)

    def _obj(n: int, body: bytes) -> bytes:
        return f"{n} 0 obj\n".encode() + body + b"\nendobj\n"

    objs = [
        _obj(1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        _obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _obj(
            3,
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        ),
        _obj(
            4,
            f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode()
            + stream
            + b"\nendstream",
        ),
        _obj(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    ]
    header = b"%PDF-1.4\n"
    body = b"".join(objs)
    xref_off = len(header) + len(body)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    off = len(header)
    for obj in objs:
        xref += f"{off:010d} 00000 n \n".encode()
        off += len(obj)
    trailer = (
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(xref_off).encode()
        + b"\n%%EOF\n"
    )
    return header + body + xref + trailer


def test_extract_pdf_text_reads_content() -> None:
    pdf = _minimal_pdf_with_text("Hello PDF")
    out = _extract_pdf_text(pdf, "demo.pdf")
    assert "Hello PDF" in out
    assert "[page 1]" in out


def test_extract_pdf_text_garbage_returns_parse_error() -> None:
    out = _extract_pdf_text(b"not a pdf", "bad.pdf")
    assert out.startswith("(could not parse bad.pdf")


def test_extract_pdf_text_empty_pages_fallback() -> None:
    # Minimal PDF with a page but no text → returns placeholder.
    pdf = _minimal_pdf_with_text("")
    out = _extract_pdf_text(pdf, "empty.pdf")
    # Either "no extractable text" or pypdf returns empty per-page — both OK.
    assert out == "(no extractable text)" or out == ""


def test_extract_pdf_text_truncates_when_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Shrink the cap so a normal tiny PDF trips the truncation branch.
    monkeypatch.setattr(
        "services.agenthub.attachments.MAX_PDF_TEXT_CHARS", 3,
    )
    pdf = _minimal_pdf_with_text("HelloWorld")
    out = _extract_pdf_text(pdf, "big.pdf")
    assert "truncated" in out


def test_extract_pdf_text_missing_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    """If pypdf isn't importable, returns a graceful placeholder."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "pypdf":
            raise ImportError("mocked")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    out = _extract_pdf_text(b"anything", "x.pdf")
    assert "pypdf not installed" in out


# ─────────────────────────────────────────────────────────────
# process_attachments
# ─────────────────────────────────────────────────────────────
def test_process_attachments_none() -> None:
    assert process_attachments(None) == ("", [], [])


def test_process_attachments_empty_list() -> None:
    assert process_attachments([]) == ("", [], [])


def test_process_attachments_text_roundtrip() -> None:
    tb, ip, warn = process_attachments(
        [{"name": "note.txt", "kind": "text", "content": "hello world"}]
    )
    assert "hello world" in tb
    assert "note.txt" in tb
    assert ip == []
    assert warn == []


def test_process_attachments_image_passthrough() -> None:
    png = b"\x89PNG\r\n\x1a\n"
    uri = "data:image/png;base64," + base64.b64encode(png).decode()
    tb, ip, warn = process_attachments(
        [{"name": "pic.png", "kind": "image", "content": uri}]
    )
    assert tb == ""
    assert len(ip) == 1
    assert ip[0]["type"] == "image_url"
    assert ip[0]["image_url"]["url"] == uri
    assert warn == []


def test_process_attachments_pdf_extracted() -> None:
    pdf = _minimal_pdf_with_text("InnerPDFText")
    uri = "data:application/pdf;base64," + base64.b64encode(pdf).decode()
    tb, ip, warn = process_attachments(
        [{"name": "doc.pdf", "kind": "pdf", "content": uri}]
    )
    assert "InnerPDFText" in tb
    assert ip == []
    assert warn == []


def test_process_attachments_rejects_empty_content() -> None:
    tb, ip, warn = process_attachments(
        [{"name": "x", "kind": "text", "content": ""}]
    )
    assert tb == "" and ip == []
    assert "empty content" in warn[0]


def test_process_attachments_rejects_oversize_single() -> None:
    big = "x" * (MAX_BYTES_PER_FILE + 1)
    tb, ip, warn = process_attachments(
        [{"name": "big.txt", "kind": "text", "content": big}]
    )
    assert tb == ""
    assert "exceeds" in warn[0]
    assert "per-file" in warn[0]


def test_process_attachments_rejects_combined_oversize() -> None:
    # Two files just under per-file cap but combined over the total.
    chunk = "x" * (MAX_BYTES_PER_FILE - 1)
    atts = [
        {"name": "a.txt", "kind": "text", "content": chunk},
        {"name": "b.txt", "kind": "text", "content": chunk},
        {"name": "c.txt", "kind": "text", "content": chunk},
    ]
    tb, ip, warn = process_attachments(atts)
    # MAX_TOTAL is 25 MB, MAX_PER_FILE is 10 MB, so the 3rd should trip.
    assert any("combined" in w for w in warn)


def test_process_attachments_rejects_bad_data_uri() -> None:
    tb, ip, warn = process_attachments(
        [{"name": "x.png", "kind": "image", "content": "not-a-data-uri"}]
    )
    assert tb == "" and ip == []
    assert "data: URI" in warn[0]


def test_process_attachments_unknown_kind() -> None:
    tb, ip, warn = process_attachments(
        [{
            "name": "mystery",
            "kind": "video",
            "content": "data:video/mp4;base64," + base64.b64encode(b"x").decode(),
        }]
    )
    assert tb == "" and ip == []
    assert "unsupported kind" in warn[0]


def test_process_attachments_non_string_content_skipped() -> None:
    tb, ip, warn = process_attachments(
        [{"name": "bad", "kind": "text", "content": 123}]
    )
    assert tb == "" and ip == []
    assert "empty content" in warn[0]


def test_process_attachments_mixed_batch() -> None:
    png = b"\x89PNG\r\n\x1a\n"
    img_uri = "data:image/png;base64," + base64.b64encode(png).decode()
    tb, ip, warn = process_attachments([
        {"name": "a.txt", "kind": "text", "content": "alpha"},
        {"name": "b.png", "kind": "image", "content": img_uri},
        {"name": "c.unknown", "kind": "???", "content": "data:x;base64,Zm9v"},
    ])
    assert "alpha" in tb
    assert len(ip) == 1
    assert any("unsupported kind" in w for w in warn)
