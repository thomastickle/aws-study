"""Deterministic standard-bank output and immutable snapshot comparison."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .bank_schema import Bank, BankQuestion


def _question_mapping(question: BankQuestion) -> dict[str, Any]:
    data: dict[str, Any] = {
        "question": question.text,
        "type": question.kind,
        "select_count": question.select_count,
        "answers": [asdict(answer) for answer in question.answers],
    }
    if question.area is not None or question.topic is not None:
        data["classification"] = {
            "area": question.area,
            "topic": question.topic,
        }
    for field in (
        "source_ref",
        "selection_group",
        "explanation",
        "verified_at",
        "verification_status",
        "source_metadata",
    ):
        value = getattr(question, field)
        if value is not None:
            data[field] = value
    if question.source_classification is not None:
        data["source_classification"] = asdict(question.source_classification)
    return data


def bank_mapping(bank: Bank) -> dict[str, Any]:
    """Produce schema-v2 JSON data without losing occurrence presentation."""
    source = asdict(bank.source)
    source["type"] = source.pop("kind")
    for field in ("metadata", "snapshot_family"):
        if source[field] is None:
            del source[field]
    return {
        "schema_version": 2,
        "certification": asdict(bank.certification),
        "source": source,
        "questions": [_question_mapping(q) for q in bank.questions],
    }


def bank_json(bank: Bank) -> str:
    """Readable UTF-8-compatible JSON, stable across repeat conversions."""
    return (
        json.dumps(
            bank_mapping(bank),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )


def snapshot_fingerprint(bank: Bank) -> str:
    """Include source content and provenance; exclude DB/manual state."""
    return hashlib.sha256(bank_json(bank).encode("utf-8")).hexdigest()
