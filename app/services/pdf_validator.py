"""Upload validation and text extraction.

Everything that can be rejected cheaply is rejected here, before a single token is spent. The
rule is that the caller learns *why* the file was refused — "not a PDF", "103 pages, limit is
100" — never a stack trace and never a silent truncation of an oversized PRD.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from pypdf import PdfReader

from app.config import MAX_PAGES, MAX_UPLOAD_BYTES, MIN_TEXT_CHARS_PER_PAGE


class PdfRejected(Exception):
    """A validation failure with an HTTP status the route can hand straight to the client."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class PdfDocument:
    raw: bytes
    filename: str
    page_count: int
    text: str

    @property
    def char_count(self) -> int:
        return len(self.text.strip())

    @property
    def has_text(self) -> bool:
        """True when pypdf found a usable text layer.

        Measured as characters *per page* rather than a flat minimum. A flat minimum cannot
        distinguish a one-page brief that is genuinely short from a ten-page scan whose only
        extractable characters are page numbers — it rejects the first along with the second.
        """
        return self.char_count >= MIN_TEXT_CHARS_PER_PAGE * self.page_count


_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase + collapse whitespace.

    Used for the cache key so that the same PRD re-exported from a different tool — different
    bytes, identical content — still hits the cache.
    """
    return _WHITESPACE.sub(" ", text).strip().lower()


def validate_and_extract(raw: bytes, filename: str) -> PdfDocument:
    if not raw:
        raise PdfRejected("The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PdfRejected(
            f"File is {len(raw) / 1_048_576:.1f} MB; the limit is {MAX_UPLOAD_BYTES // 1_048_576} MB.",
            status_code=413,
        )
    # Magic bytes, not the filename extension — a .pdf suffix proves nothing.
    if not raw.lstrip()[:5].startswith(b"%PDF-"):
        raise PdfRejected("That is not a PDF file (missing the %PDF header).")

    try:
        reader = PdfReader(io.BytesIO(raw))
        page_count = len(reader.pages)
    except Exception as exc:  # pypdf raises a family of parse errors; they all mean the same thing
        raise PdfRejected(f"The PDF could not be read: {exc}") from exc

    if page_count == 0:
        raise PdfRejected("The PDF has no pages.")
    if page_count > MAX_PAGES:
        raise PdfRejected(
            f"The PDF has {page_count} pages; the limit is {MAX_PAGES}. "
            "Split it and plan each part rather than having it silently truncated.",
            status_code=422,
        )

    chunks: list[str] = []
    for page in reader.pages:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:
            chunks.append("")  # one unreadable page must not lose the other 99

    return PdfDocument(
        raw=raw,
        filename=filename or "upload.pdf",
        page_count=page_count,
        text="\n\n".join(chunks).strip(),
    )
