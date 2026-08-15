"""Text-layer detection.

The interesting case is the boundary: telling a genuinely short document apart from a scan. An
earlier version used a flat 40-character minimum and rejected a one-page brief as "scanned",
which is the regression these tests exist to prevent.
"""

from __future__ import annotations

import pytest

from app.services.pdf_validator import PdfDocument, PdfRejected, normalize, validate_and_extract


def doc(text: str, pages: int = 1) -> PdfDocument:
    return PdfDocument(raw=b"%PDF-1.4", filename="x.pdf", page_count=pages, text=text)


def test_short_one_page_brief_counts_as_text():
    # 37 characters on one page: terse, but a real text layer.
    assert doc("Task:\nImplement User Registration API").has_text


def test_page_numbers_scattered_over_a_scan_do_not_count_as_text():
    # Same character count spread over ten pages is what a scan looks like.
    assert not doc("Task:\nImplement User Registration API", pages=10).has_text


def test_empty_text_is_not_text():
    assert not doc("   \n\n  ").has_text
    assert doc("   \n\n  ").char_count == 0


def test_char_count_ignores_surrounding_whitespace():
    assert doc("\n  hello  \n").char_count == len("hello")


def test_normalize_collapses_whitespace_and_case():
    assert normalize("  Hello\n\tWORLD  ") == "hello world"


def test_non_pdf_is_rejected_by_magic_bytes_not_extension():
    with pytest.raises(PdfRejected) as excinfo:
        validate_and_extract(b"just some text", "looks-like-a.pdf")
    assert excinfo.value.status_code == 400


def test_empty_upload_is_rejected():
    with pytest.raises(PdfRejected):
        validate_and_extract(b"", "empty.pdf")
