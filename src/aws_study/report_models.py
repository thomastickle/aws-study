"""Raw report reads and typed, self-contained inputs for presentation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

# Raw SQLite records stop at the preparation boundary in ReportService.
JsonRecord = dict[str, Any]


@dataclass(frozen=True)
class ReportSessionQuestion:
    """Saved membership, ordered answers, and an optional persisted attempt."""

    details: JsonRecord
    answers: tuple[JsonRecord, ...]
    attempt: JsonRecord | None
    selected_answer_ids: frozenset[int]


class ContextAnswer(TypedDict):
    """Full answer content in the saved display order."""

    id: int
    display_order: int
    label: str
    answer_text: str
    is_correct: bool
    rationale: str | None


class ContextSourceAnswer(TypedDict):
    """A source-order reference; absent rationale inherits displayed content."""

    answer_id: int
    source_order: int
    rationale: NotRequired[str | None]


class ContextSource(TypedDict):
    """Portable provenance, including source-specific rationale overrides."""

    source_key: str
    name: str
    source_ref: str | None
    source_order: int | None
    source_type: str
    observed_year: int | None
    verification_status: str | None
    verified_year: int | None
    verification_origin: str
    valid_from_year: int | None
    valid_to_year: int | None
    answers: list[ContextSourceAnswer]


class ContextResult(TypedDict):
    """Persisted outcome; missing elapsed/confidence measurements stay null."""

    is_correct: bool
    confidence: str | None
    elapsed_ms: int | None


class ContextQuestion(TypedDict):
    """One reconstructable question in session order."""

    id: int
    question_text: str
    question_type: str
    select_count: int
    area: str | None
    topic: str | None
    selection_group: str | None
    position: int
    flagged: bool
    answers: list[ContextAnswer]
    selected_answer_ids: list[int]
    correct_answer_ids: list[int]
    sources: list[ContextSource]
    result: ContextResult | None


class ContextSession(TypedDict):
    """Session metadata retained in context schema 3."""

    id: int
    certification_id: int
    started_at: str | None
    completed_at: str | None
    target_year: int | None
    mode: str
    strategy: str
    requested_count: int | None
    session_label: str | None
    source_kind: str
    notes: str | None
    provider: str
    code: str
    cert_name: str


class PreparedAttempt(ContextResult):
    """An attempted question with the choices and provenance used in reports."""

    id: int
    session_id: int
    question_id: int
    attempted_at: str | None
    source_kind: str
    note: str | None
    question_text: str
    area: str | None
    topic: str | None
    sources: list[ContextSource]
    selected: list[str]
    correct: list[str]


class SessionData(TypedDict):
    """Single owner of complete prepared context and review records."""

    session: ContextSession
    attempts: list[PreparedAttempt]
    questions: tuple[ContextQuestion, ...]


class ContextExport(TypedDict):
    """The context JSON envelope, independent of the database schema version."""

    schema_version: Literal[3]
    purpose: str
    session: ContextSession
    continuation_prompt: str
    questions: tuple[ContextQuestion, ...]
