"""Report read models, including curated classification and provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

# Export records retain column names and JSON-compatible metadata.
JsonRecord = dict[str, Any]


@dataclass(frozen=True)
class ReportChoice:
    """Raw answer text and its saved session display letter, before presentation."""

    label: str | None
    text: str


@dataclass(frozen=True)
class ReportAttempt:
    """Attempt metadata with choices in their original option order."""

    details: JsonRecord
    selected: tuple[ReportChoice, ...]
    correct: tuple[ReportChoice, ...]


class SessionData(TypedDict):
    """Session/certification metadata and attempts with choice text lists."""

    session: JsonRecord
    attempts: list[JsonRecord]


@dataclass(frozen=True)
class ReportBundle:
    """All data needed to render files after the database connection closes."""

    data: SessionData
    questions: tuple[JsonRecord, ...]
