"""The uploaded PRD, as the rest of the application sees it.

This lives in `domain/` rather than in the validator that produces it because the ports depend on
it: `LlmRequest` carries an optional `PdfDocument`, and a port that imported from `services/` would
invert the dependency the layering exists to enforce. Nothing here does I/O or knows about pypdf —
it is a value object with two derived properties.

`app.services.pdf_validator` re-exports it, so the import path callers already use still works.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import MIN_TEXT_CHARS_PER_PAGE


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


__all__ = ["PdfDocument"]
