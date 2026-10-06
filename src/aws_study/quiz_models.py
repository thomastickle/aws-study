"""Quiz data shared by presentation, behavior, and persistence code."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    """An answer choice, with its stored explanation and correctness."""

    id: int
    label: str
    text: str
    correct: bool
    rationale: str | None


@dataclass(frozen=True)
class Question:
    """A question with choices in presentation order."""

    id: int
    text: str
    kind: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class AnswerResult:
    """The outcome of a recorded answer, ready for presentation."""

    question_id: int
    is_correct: bool
    selected_options: tuple[Option, ...]
    correct_options: tuple[Option, ...]


@dataclass(frozen=True)
class AttemptHistory:
    """The fields needed to weight a question's recent attempts."""

    is_correct: bool
    confidence: str | None
    attempted_at: str | None


@dataclass(frozen=True)
class QuestionHistory:
    """Aggregate history plus up to five attempts, newest first."""

    question_id: int
    attempt_count: int
    miss_count: int
    recent_attempts: tuple[AttemptHistory, ...]
