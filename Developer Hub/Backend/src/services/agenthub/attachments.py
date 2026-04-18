"""
Prompt-attachment processing.

Accepts the structured ``attachments`` array posted by the frontend alongside
a new-session request and turns it into two pieces:

* ``text_block`` — a single string of fenced code blocks derived from text
  files and PDF text extraction, to be concatenated onto the user prompt so
  both the planner and downstream agents see the content.
* ``image_parts`` — OpenAI-style multi-part ``image_url`` content parts to
  be appended to the planner's user message so GPT-4o vision can read them.

The frontend is responsible for encoding content:

* ``kind == "text"``   → ``content`` is the raw UTF-8 file contents.
* ``kind == "image"``  → ``content`` is a base64 data URI
  (``data:image/png;base64,...``).
* ``kind == "pdf"``    → ``content`` is a base64 data URI
  (``data:application/pdf;base64,...``).

We enforce conservative size limits here as a server-side backstop; the
frontend also rejects oversized files before upload.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Per-file and total byte caps applied after base64 decoding.
MAX_BYTES_PER_FILE = 10 * 1024 * 1024  # 10 MB
MAX_TOTAL_BYTES = 25 * 1024 * 1024  # 25 MB combined
# How much extracted PDF text we're willing to inline into the prompt.
MAX_PDF_TEXT_CHARS = 60_000


def _decode_data_uri(data_uri: str) -> tuple[str, bytes]:
    """Return ``(mime, bytes)`` for a ``data:<mime>;base64,<payload>`` URI."""
    if not data_uri.startswith("data:"):
        raise ValueError("Attachment content must be a data: URI")
    header, _, payload = data_uri.partition(",")
    if ";base64" not in header:
        raise ValueError("Attachment must be base64-encoded")
    mime = header[len("data:"):].split(";", 1)[0] or "application/octet-stream"
    try:
        raw = base64.b64decode(payload, validate=False)
    except Exception as exc:  # pragma: no cover — defensive
        raise ValueError(f"Could not decode base64 payload: {exc}") from exc
    return mime, raw


def _extract_pdf_text(raw: bytes, name: str) -> str:
    try:
        # Imported lazily so environments without pypdf (e.g. minimal test
        # containers) still start — only PDF attachments require it.
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        logger.warning("[ATTACHMENTS] pypdf not installed — skipping PDF %s", name)
        return f"(pypdf not installed — could not extract text from {name})"

    try:
        reader = PdfReader(io.BytesIO(raw))
    except Exception as exc:
        logger.warning("[ATTACHMENTS] Could not parse PDF %s: %s", name, exc)
        return f"(could not parse {name}: {exc})"

    parts: list[str] = []
    total = 0
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:  # pragma: no cover — pypdf edge cases
            text = ""
        if not text:
            continue
        parts.append(f"[page {i + 1}]\n{text}")
        total += len(text)
        if total >= MAX_PDF_TEXT_CHARS:
            parts.append("… (truncated)")
            break
    return "\n\n".join(parts) if parts else "(no extractable text)"


def process_attachments(
    attachments: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Split ``attachments`` into a text block and image content parts.

    Returns ``(text_block, image_parts, warnings)``.

    * ``text_block`` is the empty string if there are no text / PDF
      attachments; otherwise it's a sequence of fenced markdown blocks
      ready to be appended to the user prompt.
    * ``image_parts`` is a list of OpenAI-style
      ``{"type": "image_url", "image_url": {"url": ...}}`` dicts.
    * ``warnings`` is a list of human-readable messages about any
      attachments we skipped or truncated.
    """
    if not attachments:
        return "", [], []

    text_chunks: list[str] = []
    image_parts: list[dict[str, Any]] = []
    warnings: list[str] = []
    running_bytes = 0

    for att in attachments:
        name = str(att.get("name") or "attachment")
        kind = str(att.get("kind") or "").lower()
        content = att.get("content")
        if not isinstance(content, str) or not content:
            warnings.append(f"Skipped {name}: empty content.")
            continue

        # Decode size up-front for the enforcement check. Text is UTF-8, so
        # use the encoded length; binary is the base64 payload length.
        try:
            if kind == "text":
                raw_bytes = content.encode("utf-8", errors="replace")
            else:
                _mime, raw_bytes = _decode_data_uri(content)
        except ValueError as exc:
            warnings.append(f"Skipped {name}: {exc}")
            continue

        if len(raw_bytes) > MAX_BYTES_PER_FILE:
            warnings.append(
                f"Skipped {name}: {len(raw_bytes) // 1024} KB exceeds "
                f"{MAX_BYTES_PER_FILE // (1024 * 1024)} MB per-file limit."
            )
            continue
        if running_bytes + len(raw_bytes) > MAX_TOTAL_BYTES:
            warnings.append(
                f"Skipped {name}: combined attachment size would exceed "
                f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB total limit."
            )
            continue
        running_bytes += len(raw_bytes)

        if kind == "text":
            text_chunks.append(
                f"\n\n--- Attached file: {name} ---\n```\n{content}\n```"
            )
        elif kind == "pdf":
            extracted = _extract_pdf_text(raw_bytes, name)
            text_chunks.append(
                f"\n\n--- Attached PDF: {name} (extracted text) ---\n```\n{extracted}\n```"
            )
        elif kind == "image":
            # Pass the original data URI straight through — GPT-4o vision
            # accepts ``data:`` URIs directly in ``image_url.url``.
            image_parts.append(
                {"type": "image_url", "image_url": {"url": content}}
            )
        else:
            warnings.append(f"Skipped {name}: unsupported kind '{kind}'.")

    return "".join(text_chunks), image_parts, warnings
