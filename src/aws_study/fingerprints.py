"""Versioned, conservative matching; stored source text is never rewritten."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable

FINGERPRINT_VERSION = 1


def normalize_match_text(value: str) -> str:
    """Normalize Unicode, whitespace and case, retaining punctuation."""
    value = unicodedata.normalize("NFKC", value).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_fingerprint(stem: str, answers: Iterable[str]) -> str:
    """Match complete unordered content independently of answer correctness."""
    return _digest(
        {
            "stem": normalize_match_text(stem),
            "answers": sorted(normalize_match_text(text) for text in answers),
        }
    )


def answer_key_fingerprint(answers: Iterable[str]) -> str:
    """Hash the unordered correct-answer set for conflict detection."""
    return _digest(sorted(normalize_match_text(text) for text in answers))
