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
    select_count: int = 1


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
    selection_group: str | None = None
    normalized_stem: str | None = None


@dataclass(frozen=True)
class DraftResponse:
    """Persisted exam state, without correctness or feedback."""

    question_id: int
    selected: frozenset[int] = frozenset()
    confidence: str | None = None
    elapsed_ms: int | None = 0
    flagged: bool = False
    first_answered_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class SessionReviewItem:
    """One saved question and its draft, in the original quiz order."""

    number: int
    question: Question
    response: DraftResponse

    @property
    def status(self) -> str:
        if not self.response.selected:
            return "UNANSWERED"
        option_ids = {o.id for o in self.question.options}
        if (
            len(self.response.selected) != self.question.select_count
            or not self.response.selected <= option_ids
        ):
            return "INCOMPLETE"
        return "ANSWERED"

    @property
    def selected_labels(self) -> tuple[str, ...]:
        return tuple(
            o.label
            for o in self.question.options
            if o.id in self.response.selected
        )


@dataclass(frozen=True)
class SavedExam:
    """A resumable session summary without any grading information."""

    session_id: int
    started_at: str | None
    answered: int
    total: int
    flagged: int
