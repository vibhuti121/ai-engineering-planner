"""Content-addressed plan ids.

The plan's id is *derived from its input*, which is what lets storage and caching be the same
component: a second upload of the same PRD computes the same id, finds the plan already in the
store, and returns it without calling the model.

The correctness argument for the cache is entirely in this function: **every input that can
change the output is in the key, and nothing that cannot, is.**

* ``normalized text`` — the PRD's content, not its bytes. Re-exporting the same document from a
  different tool changes the bytes but not the plan, and should still hit.
* ``prompt_version`` — editing ``system_prompt.txt`` changes the output, so it must invalidate.
* ``model`` — a different model is a different plan.
* ``provider_kind`` — the API adapter sends the PDF natively while the CLI adapter sends extracted
  text, so the two can legitimately differ. They must not share a cache slot.

Deliberately *not* in the key: the filename, the upload timestamp, and the raw bytes. None of them
change the plan, and including any of them would turn every re-upload into a miss.
"""

from __future__ import annotations

import hashlib

from app.config import PROMPT_VERSION
from app.services.pdf_validator import PdfDocument, normalize


def compute_plan_id(document: PdfDocument, model: str, provider_kind: str) -> str:
    if document.has_text:
        content = normalize(document.text).encode("utf-8")
    else:
        # Scanned/image-only PDF: nothing to normalize, so fall back to the bytes. Two exports of
        # the same scan will miss — correct, because we cannot prove they are the same document.
        content = hashlib.sha256(document.raw).hexdigest().encode("ascii")

    digest = hashlib.sha256()
    for part in (content, PROMPT_VERSION.encode(), model.encode(), provider_kind.encode()):
        digest.update(part)
        digest.update(b"\x00")  # separator: prevents "ab"+"c" colliding with "a"+"bc"
    return digest.hexdigest()[:16]
