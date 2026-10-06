"""Normalized records passed from the JSON importer to persistence."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportedOption:
    """An option's content and selection in the original assessment."""

    label: str | None
    text: str
    correct: bool
    selected: bool
    rationale: str | None


@dataclass(frozen=True)
class ImportedQuestion:
    """Question metadata after classification and hashing, before storage."""

    certification_id: int
    source_id: int
    source_question_number: int | None
    external_key: str
    question_text: str
    question_type: str
    topic: str
    concept: str
    valid_from_year: int | None
    verification_status: str
    verified_year: int | None
    dedup_group: str | None
    dedup_role: str
    variant_group: str | None
    content_hash: str
    metadata_json: str
