"""Content-addressed keys for the three-stage pipeline.

Every key is derived from its input, which is what lets storage and caching be the same component:
the same input computes the same key, finds the artifact already on disk, and skips the model call.

The whole correctness argument for the cache is in this module: **every input that can change a
stage's output is in that stage's key, and nothing that cannot, is.**

A stage's key folds in four things:

* ``parent`` — the key of the stage before it, so the chain is a chain. A graph derived from a
  different understanding is a different graph.
* ``payload_hash`` — the canonical bytes this stage actually consumed. The parent key alone is not
  enough: models are non-deterministic and ``refresh`` exists, so two runs can share a ``read_key``
  and still produce different understandings. Without this, a stale graph could attach itself to a
  freshly regenerated understanding.
* ``prompt_version`` — *this stage's* prompt, content-hashed by ``app.prompts``.
* ``model`` and ``kind`` — a different model is a different answer, and the CLI adapter sends
  extracted text where the API adapter sends the PDF natively, so the two must not share a slot.

Deliberately *not* in any key: the filename, the upload timestamp, the raw bytes of a text-bearing
PDF. None change the output, and including any of them would turn every re-upload into a miss.

The payoff, which a single global prompt version could never express: editing only the verifier's
prompt moves ``verify_key`` and therefore ``plan_id``, but leaves ``read_key`` and ``graph_key``
untouched — so stages 1 and 2 load from disk and only stage 3 re-runs.

    read_key   = stage_key("read",   parent=doc_id,    payload_hash="",              pv=read_pv)
    graph_key  = stage_key("graph",  parent=read_key,  payload_hash=H(understanding), pv=graph_pv)
    verify_key = stage_key("verify", parent=graph_key, payload_hash=H(ordered_plan),  pv=verify_pv)
    plan_id    = verify_key          # the plan id IS the terminal link of the chain
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.domain.document import PdfDocument
from app.services.pdf_validator import normalize

KEY_LENGTH = 16
"""64 bits of a SHA-256. Short enough to read in a log line and paste into a URL; a birthday
collision needs ~4 billion distinct plans, which is not this application's problem."""


def _digest(*parts: str) -> str:
    """SHA-256 over NUL-separated parts. The separator is what stops "ab"+"c" colliding with "a"+"bc"."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:KEY_LENGTH]


def doc_id(document: PdfDocument) -> str:
    """Identity of the PRD itself — independent of prompt, model and provider.

    This is the artifact *folder* name, so re-planning the same document with a changed prompt
    files everything under one directory instead of scattering it.
    """
    if document.has_text:
        content = normalize(document.text)
    else:
        # Scanned/image-only PDF: nothing to normalize, so fall back to the bytes. Two exports of
        # the same scan will miss — correct, because we cannot prove they are the same document.
        content = hashlib.sha256(document.raw).hexdigest()
    return _digest(content)


def content_hash(payload: Any) -> str:
    """Hash of a JSON-able payload, canonicalised so key order and whitespace cannot matter.

    Pydantic dumps dict keys in field-declaration order and json.dumps would preserve that, so two
    equal payloads could hash differently after a harmless model-field reorder. `sort_keys` removes
    that whole class of spurious miss.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _digest(canonical)


def stage_key(
    stage: str,
    *,
    parent: str,
    payload_hash: str,
    prompt_version: str,
    model: str,
    kind: str,
) -> str:
    return _digest(stage, parent, payload_hash, prompt_version, model, kind)


def intent_key(doc: str, prompt_versions: dict[str, str], model: str, kind: str) -> str:
    """The L1 (in-memory) key — everything the run intends, computable *before* any model call.

    The chained keys cannot serve the "re-upload is instant" path, because ``graph_key`` depends on
    bytes that stage 1 has not produced yet. This one depends on nothing but the PDF and the
    configuration, so it can be looked up first. On a hit the stored plan is returned whole; on a
    miss the chain runs and the disk cache still gives partial reuse.
    """
    versions = "|".join(f"{name}={prompt_versions[name]}" for name in sorted(prompt_versions))
    return _digest("intent", doc, versions, model, kind)


__all__ = ["KEY_LENGTH", "content_hash", "doc_id", "intent_key", "stage_key"]
