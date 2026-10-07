"""Report read models, including curated classification and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

# Export records retain column names and JSON-compatible metadata.
JsonRecord = dict[str, Any]


@dataclass(frozen=True)
class ReportSessionQuestion:
    """Saved membership, ordered answers, and an optional persisted attempt."""

    details: JsonRecord
    answers: tuple[JsonRecord, ...]
    attempt: JsonRecord | None
    selected_answer_ids: frozenset[int]


class SessionData(TypedDict):
    """Complete session context and attempted-question review records."""

    session: JsonRecord
    attempts: list[JsonRecord]
    questions: tuple[JsonRecord, ...]


@dataclass(frozen=True)
class ReportBundle:
    """All data needed to render files after the database connection closes."""

    data: SessionData
    questions: tuple[JsonRecord, ...]
