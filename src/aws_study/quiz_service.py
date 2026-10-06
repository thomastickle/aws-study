"""Quiz selection, scoring, and session lifecycle without terminal I/O."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .quiz_models import AnswerResult, Question
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
            if not selected:
                raise ValueError("Choose at least one option.")
            option_ids = {option.id for option in question.options}
            if not selected.issubset(option_ids):
                raise ValueError("Unknown answer choice.")
            if confidence not in (None, "high", "medium", "low"):
                raise ValueError("Unknown confidence.")
            if elapsed_ms < 0:
                raise ValueError("Elapsed time cannot be negative.")
            correct_options = tuple(o for o in question.options if o.correct)
            is_correct = selected == {o.id for o in correct_options}
            self._repository.insert_attempt(
                session_id,
                question_id,
                selected,
                attempted_at=_now(),
                is_correct=is_correct,
                confidence=confidence,
                elapsed_ms=elapsed_ms,
            )
            return AnswerResult(
                question_id,
                is_correct,
                tuple(o for o in question.options if o.id in selected),
                correct_options,
            )

    def finish_session(self, session_id: int) -> None:
        """Complete a fully answered session; repeated calls are harmless."""
        with self._repository.transaction():
            self._repository.lock_session(session_id)
            if self._repository.is_complete(session_id):
                return
            if self._repository.unanswered_questions(session_id):
                raise ValueError("Answer every question before finishing.")
            self._repository.complete_session(session_id, _now())
