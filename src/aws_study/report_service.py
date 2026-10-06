"""Assemble report inputs and choose reinforcement questions."""
from __future__ import annotations

from .report_models import ReportBundle, ReportChoice, SessionData
from .report_repository import ReportRepository


def _choice_text(choice: ReportChoice) -> str:
    # Preserve stored letters and unlabeled options in existing report exports.
    prefix = f"{choice.label}. " if choice.label is not None else ""
    return prefix + choice.text


class ReportService:
    """Build presentation inputs without terminal or filesystem I/O."""

    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def resolve_session(self, value: str) -> int:
        """Accept a numeric ID or the latest interactive session."""
        return (
            self._repository.latest_interactive_session()
            if value == "latest" else int(value)
        )

    def session_data(self, session_id: int) -> SessionData:
        """Assemble the established session/attempt shape for prompts."""
        session = self._repository.session(session_id)
        attempts = []
        for attempt in self._repository.attempts(session_id):
            details = dict(attempt.details)
            details["selected"] = [_choice_text(o) for o in attempt.selected]
            details["correct"] = [_choice_text(o) for o in attempt.correct]
            attempts.append(details)
        return {"session": session, "attempts": attempts}

    def bundle(self, session_id: int) -> ReportBundle:
        """Include exact content only for misses and low-confidence answers."""
        data = self.session_data(session_id)
        question_ids = sorted({
            attempt["question_id"] for attempt in data["attempts"]
            if not attempt["is_correct"] or attempt.get("confidence") == "low"
        })
        return ReportBundle(data, tuple(
            self._repository.question_context(question_id)
            for question_id in question_ids
        ))
