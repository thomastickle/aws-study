"""Strict schema-v2 input validation, independent of database and UI."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fingerprints import normalize_match_text

VERIFICATION_STATUSES = (
    "official_current",
    "verified_current",
    "official_older",
    "unverified",
    "stale",
    "superseded",
)


class BankValidationError(ValueError):
    """All invalid records discovered before any import writes."""

    def __init__(self, errors: list[str], questions_seen: int = 0):
        self.errors = errors
        self.questions_seen = questions_seen
        super().__init__("Invalid bank: " + "; ".join(errors))


@dataclass(frozen=True)
class BankAnswer:
    """Answer wording, grading key and optional source rationale."""

    text: str
    correct: bool
    rationale: str | None


@dataclass(frozen=True)
class BankQuestion:
    """Validated question content and its optional source reference."""

    text: str
    kind: str
    select_count: int
    area: str | None
    topic: str | None
    answers: tuple[BankAnswer, ...]
    source_ref: str | None
    selection_group: str | None = None


@dataclass(frozen=True)
class Bank:
    """Validated, independently importable source and certification metadata."""

    certification: dict[str, Any]
    source: dict[str, Any]
    questions: tuple[BankQuestion, ...]


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not normalize_match_text(value):
        raise ValueError(f"{field} must be nonempty text")
    return value


def validate_question(record: Any, *, curated: bool = True) -> BankQuestion:
    """Validate source content; migration may omit curated classification."""
    if not isinstance(record, dict):
        raise ValueError("question must be an object")
    forbidden = {
        "status",
        "selected",
        "confidence",
        "original_confidence",
        "time_to_answer_seconds",
        "id",
        "dedup_role",
        "dedup_group",
        "variant_group",
        "content_hash",
        "content_fingerprint",
        "answer_key_fingerprint",
        "fingerprint_version",
        "tags",
        "elapsed_ms",
        "selected_answers",
        "question_id",
    }
    if forbidden.intersection(record):
        raise ValueError("runtime/history fields do not belong in a v2 bank")
    text = _text(record.get("question"), "question")
    kind = record.get("type")
    count = record.get("select_count")
    if kind not in ("single_select", "multi_select"):
        raise ValueError("unknown question type")
    if type(count) is not int or count < 1:
        raise ValueError("select_count must be a positive integer")
    if (
        kind == "single_select"
        and count != 1
        or kind == "multi_select"
        and count < 2
    ):
        raise ValueError("select_count disagrees with question type")
    raw_answers = record.get("answers")
    if not isinstance(raw_answers, list) or len(raw_answers) < 2:
        raise ValueError("at least two answers are required")
    answers = []
    for raw in raw_answers:
        if not isinstance(raw, dict) or type(raw.get("correct")) is not bool:
            raise ValueError("each answer needs a boolean correct field")
        if {"selected", "id", "content_hash"}.intersection(raw):
            raise ValueError("runtime/history fields do not belong in answers")
        rationale = raw.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise ValueError("answer rationale must be text or null")
        answers.append(
            BankAnswer(
                _text(raw.get("text"), "answer text"),
                raw["correct"],
                rationale,
            )
        )
    if len({normalize_match_text(a.text) for a in answers}) != len(answers):
        raise ValueError("duplicate normalized answer text")
    if sum(a.correct for a in answers) != count:
        raise ValueError("correct answer count disagrees with select_count")
    classification = record.get("classification", {})
    if not isinstance(classification, dict):
        raise ValueError("classification must be an object")
    area, topic = classification.get("area"), classification.get("topic")
    if curated:
        area = _text(area, "classification.area")
        topic = _text(topic, "classification.topic")
    ref = record.get("source_ref")
    if ref is not None:
        ref = _text(ref, "source_ref")
    group = record.get("selection_group")
    if group is not None:
        group = _text(group, "selection_group")
    return BankQuestion(
        text, kind, count, area, topic, tuple(answers), ref, group
    )


def load_bank(path: str | Path) -> Bank:
    """Read a complete v2 file and report all invalid question records."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 2
    ):
        raise BankValidationError(
            [
                "Only schema_version 2 is accepted; migrate legacy database "
                "history with migrate-v1-to-v2 and supply curated v2 banks."
            ]
        )
    certification, source = data.get("certification"), data.get("source")
    if not isinstance(certification, dict) or not isinstance(source, dict):
        raise BankValidationError(
            ["certification and source objects are required"]
        )
    try:
        for field in ("provider", "code", "name"):
            _text(certification.get(field), f"certification.{field}")
        for field in ("key", "name", "type"):
            _text(source.get(field), f"source.{field}")
        for field in ("observed_year", "verified_year"):
            value = source.get(field)
            if value is not None and (
                type(value) is not int or not 1 <= value <= 9999
            ):
                raise ValueError(f"source.{field} must be a year or null")
        if source.get("verification_status") not in VERIFICATION_STATUSES:
            raise ValueError("unknown source verification_status")
    except ValueError as error:
        raise BankValidationError([str(error)]) from error
    records = data.get("questions")
    if not isinstance(records, list) or not records:
        raise BankValidationError(["questions must be a nonempty array"])
    questions, errors, refs = [], [], set()
    for position, record in enumerate(records, 1):
        try:
            question = validate_question(record)
            if question.source_ref is not None:
                if question.source_ref in refs:
                    raise ValueError("duplicate source_ref")
                refs.add(question.source_ref)
            questions.append(question)
        except ValueError as error:
            errors.append(f"Question {position}: {error}")
    if errors:
        raise BankValidationError(errors, len(records))
    return Bank(certification, source, tuple(questions))
