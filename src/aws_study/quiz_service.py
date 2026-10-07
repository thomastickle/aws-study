"""Quiz selection, scoring, and session lifecycle without terminal I/O."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .quiz_models import (
    AnswerResult,
    DraftResponse,
    Question,
    SavedExam,
    SessionReviewItem,
)
from .quiz_repository import QuizRepository
from .selection import rank_candidates, weighted_sample


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QuizService:
    """Coordinate quiz behavior and the transactions needed to persist it."""

    def __init__(self, repository: QuizRepository) -> None:
        self._repository = repository

    def create_session(
        self,
        cert_id: int,
        *,
        count: int,
        target_year: int | None,
        mode: str,
        strategy: str,
        seed: int | None = None,
        include_unverified: bool = False,
    ) -> int:
        """Choose questions and atomically save shuffled answers for this session."""
        if count < 1:
            raise ValueError("Question count must be at least 1.")
        if mode not in ("study", "exam"):
            raise ValueError("Unknown quiz mode.")
        if strategy not in ("adaptive", "random", "weak", "new"):
            raise ValueError("Unknown selection strategy.")
        history = self._repository.candidate_history(
            cert_id,
            target_year=target_year,
            include_unverified=include_unverified,
            include_recent=strategy in ("adaptive", "weak"),
        )
        pool = rank_candidates(history, strategy=strategy)
        if not pool:
            raise ValueError(
                "No eligible questions. Check certification/year/"
                "verification filters."
            )
        picked = weighted_sample(pool, min(count, len(pool)), seed=seed)
        rng = random.Random(
            f"answer-order:{seed}" if seed is not None else None
        )
        with self._repository.transaction():
            session_id = self._repository.insert_session(
                cert_id,
                started_at=_now(),
                target_year=target_year,
                mode=mode,
                strategy=strategy,
                requested_count=count,
                selected_questions=[
                    (item.question_id, item.weight) for item in picked
                ],
            )
            question_ids = [item.question_id for item in picked]
            answers = self._repository.answer_ids(question_ids)
            for question_id in question_ids:
                answer_ids = answers[question_id]
                rng.shuffle(answer_ids)
                self._repository.save_answer_order(
                    session_id, question_id, answer_ids
                )
            return session_id

    def is_complete(self, session_id: int) -> bool:
        """Return whether a session has been finalized."""
        return self._repository.is_complete(session_id)

    def questions(self, session_id: int) -> tuple[Question, ...]:
        """Return saved questions as models in session order."""
        self._repository.is_complete(session_id)
        return self._repository.questions(session_id)

    def record_answer(
        self,
        session_id: int,
        question_id: int,
        selected: set[int],
        confidence: str | None,
        elapsed_ms: int,
    ) -> AnswerResult:
        """Score option IDs and atomically record the attempt and choices."""
        with self._repository.transaction():
            self._repository.lock_session(session_id)
            if self._repository.is_complete(session_id):
                raise ValueError("Session is already complete.")
            question = self._repository.question(session_id, question_id)
            if self._repository.has_attempt(session_id, question_id):
                raise ValueError("This question has already been answered.")
            if self._repository.session(session_id)["mode"] != "study":
                raise ValueError(
                    "Exam answers must be saved as drafts until submission."
                )
            self.validate_selection(question, selected)
            if confidence not in (None, "high", "medium", "low"):
                raise ValueError("Unknown confidence.")
            if elapsed_ms < 0:
                raise ValueError("Elapsed time cannot be negative.")
            result = self._result(question, selected)
            self._repository.insert_attempt(
                session_id,
                question_id,
                selected,
                attempted_at=_now(),
                is_correct=result.is_correct,
                confidence=confidence,
                elapsed_ms=elapsed_ms,
            )
            return result

    def finish_session(self, session_id: int) -> None:
        """Complete a fully answered session; repeated calls are harmless."""
        with self._repository.transaction():
            self._repository.lock_session(session_id)
            if self._repository.is_complete(session_id):
                return
            if self._repository.session(session_id)["mode"] == "exam":
                raise ValueError("Use final submission to finish an exam.")
            if self._repository.unanswered_questions(session_id):
                raise ValueError("Answer every question before finishing.")
            self._repository.complete_session(session_id, _now())

    @staticmethod
    def validate_selection(question: Question, selected: set[int]) -> None:
        """The stored select_count is authoritative for every answer."""
        if not selected <= {o.id for o in question.options}:
            raise ValueError("Unknown answer choice.")
        if len(selected) != question.select_count:
            raise ValueError(
                f"This question requires exactly {question.select_count} selections; "
                f"you entered {len(selected)}."
            )

    def _editable_exam(self, session_id: int) -> None:
        self._repository.lock_session(session_id)
        session = self._repository.session(session_id)
        if (
            session["mode"] != "exam"
            or session["source_kind"] != "interactive"
        ):
            raise ValueError("Only interactive exam sessions support drafts.")
        if session["completed_at"] is not None:
            raise ValueError("Session is already complete.")

    def review_items(self, session_id: int) -> tuple[SessionReviewItem, ...]:
        """Return saved questions and draft states without correctness feedback."""
        questions = self.questions(session_id)
        responses = self._repository.responses(session_id)
        return tuple(
            SessionReviewItem(
                number,
                question,
                responses.get(question.id, DraftResponse(question.id)),
            )
            for number, question in enumerate(questions, 1)
        )

    def resume_session(self, session_id: int) -> tuple[SessionReviewItem, ...]:
        """Validate resumability without selecting or shuffling new questions."""
        with self._repository.transaction():
            self._editable_exam(session_id)
            items = self.review_items(session_id)
            if not items:
                raise ValueError("Session has no saved questions.")
            return items

    def saved_exams(self, cert_id: int) -> tuple[SavedExam, ...]:
        """List draft progress for one certification, without grading."""
        return self._repository.unfinished_exams(cert_id)

    def save_response(
        self,
        session_id: int,
        question_id: int,
        selected: set[int],
        elapsed_ms: int = 0,
    ) -> None:
        """Save a valid draft immediately, leaving its confidence unchanged."""
        if elapsed_ms < 0:
            raise ValueError("Elapsed time cannot be negative.")
        with self._repository.transaction():
            self._editable_exam(session_id)
            question = self._repository.question(session_id, question_id)
            self.validate_selection(question, selected)
            now = _now()
            self._repository.save_response(
                session_id, question_id, selected, now
            )
            if elapsed_ms:
                self._repository.add_elapsed(
                    session_id, question_id, elapsed_ms, now
                )

    def set_confidence(
        self, session_id: int, question_id: int, confidence: str | None
    ) -> None:
        """Update or clear draft confidence without changing its answer."""
        if confidence not in (None, "high", "medium", "low"):
            raise ValueError("Unknown confidence.")
        with self._repository.transaction():
            self._editable_exam(session_id)
            self._repository.question(session_id, question_id)
            self._repository.update_confidence(
                session_id, question_id, confidence, _now()
            )

    def toggle_flag(self, session_id: int, question_id: int) -> None:
        """Toggle a review flag without creating an attempt."""
        with self._repository.transaction():
            self._editable_exam(session_id)
            self._repository.question(session_id, question_id)
            self._repository.toggle_flag(session_id, question_id, _now())

    def add_elapsed(
        self, session_id: int, question_id: int, elapsed_ms: int
    ) -> None:
        """Persist active-question time, excluding review and offline time."""
        if elapsed_ms < 0:
            raise ValueError("Elapsed time cannot be negative.")
        with self._repository.transaction():
            self._editable_exam(session_id)
            self._repository.question(session_id, question_id)
            if elapsed_ms:
                self._repository.add_elapsed(
                    session_id, question_id, elapsed_ms, _now()
                )

    def submission_errors(self, session_id: int) -> tuple[str, ...]:
        """Explain missing or invalid drafts without revealing correct answers."""
        return self._submission_errors(self.review_items(session_id))

    @staticmethod
    def _submission_errors(
        items: tuple[SessionReviewItem, ...],
    ) -> tuple[str, ...]:
        return tuple(
            f"Question {item.number} has not been answered."
            if item.status == "UNANSWERED"
            else f"Question {item.number} requires {item.question.select_count} selections "
            f"but currently has {len(item.response.selected)}."
            for item in items
            if item.status != "ANSWERED"
        )

    @staticmethod
    def _result(question: Question, selected: set[int]) -> AnswerResult:
        correct = tuple(o for o in question.options if o.correct)
        return AnswerResult(
            question.id,
            selected == {o.id for o in correct},
            tuple(o for o in question.options if o.id in selected),
            correct,
        )

    def submit_session(self, session_id: int) -> tuple[AnswerResult, ...]:
        """Atomically finalize every draft; repeated submissions return existing results."""
        with self._repository.transaction():
            self._repository.lock_session(session_id)
            session = self._repository.session(session_id)
            if (
                session["mode"] != "exam"
                or session["source_kind"] != "interactive"
            ):
                raise ValueError(
                    "Only interactive exams support final submission."
                )
            if session["completed_at"] is not None:
                finalized = self._repository.attempt_selections(session_id)
                return tuple(
                    self._result(q, finalized.get(q.id, set()))
                    for q in self.questions(session_id)
                )
            items = self.review_items(session_id)
            if not items:
                raise ValueError("Session has no saved questions.")
            errors = self._submission_errors(items)
            if errors:
                raise ValueError("Cannot submit.\n" + "\n".join(errors))
            now = _now()
            results = []
            for item in items:
                selected = set(item.response.selected)
                result = self._result(item.question, selected)
                self._repository.insert_attempt(
                    session_id,
                    item.question.id,
                    selected,
                    attempted_at=now,
                    is_correct=result.is_correct,
                    confidence=item.response.confidence,
                    elapsed_ms=item.response.elapsed_ms,
                )
                results.append(result)
            self._repository.complete_session(session_id, now)
            return tuple(results)
