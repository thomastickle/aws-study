"""Assemble full session context and attempted-question review records."""

from __future__ import annotations

from .report_models import JsonRecord, ReportBundle, SessionData
from .report_repository import ReportRepository


def _choice_text(answer: JsonRecord) -> str:
    return f"{answer['label']}. {answer['answer_text']}"


class ReportService:
    """Build presentation inputs without terminal or filesystem I/O."""

    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def resolve_session(self, value: str) -> int:
        """Accept a numeric ID or the latest interactive session."""
        return (
            self._repository.latest_interactive_session()
            if value == "latest"
            else int(value)
        )

    def session_data(self, session_id: int) -> SessionData:
        """Build all exports from the same saved session membership."""
        session = self._repository.session(session_id)
        if (
            session["mode"] == "exam"
            and session["source_kind"] == "interactive"
            and session["completed_at"] is None
        ):
            raise ValueError(
                f"Draft exam {session_id} is not submitted. "
                f"Resume with quiz --resume {session_id}."
            )
        questions = []
        attempts = []
        for record in self._repository.session_questions(session_id):
            question = dict(record.details)
            question["answers"] = list(record.answers)
            question["selected_answers"] = [
                answer
                for answer in record.answers
                if answer["id"] in record.selected_answer_ids
            ]
            question["correct_answers"] = [
                answer for answer in record.answers if answer["is_correct"]
            ]
            question["result"] = None
            if record.attempt is not None:
                question["result"] = {
                    "is_correct": bool(record.attempt["is_correct"]),
                    "confidence": record.attempt["confidence"],
                    "elapsed_ms": record.attempt["elapsed_ms"],
                }
                attempt = dict(record.attempt)
                for key in ("question_text", "area", "topic", "sources"):
                    attempt[key] = question[key]
                attempt["selected"] = [
                    _choice_text(answer)
                    for answer in question["selected_answers"]
                ]
                attempt["correct"] = [
                    _choice_text(answer)
                    for answer in question["correct_answers"]
                ]
                attempts.append(attempt)
            questions.append(question)
        return {
            "session": session,
            "attempts": attempts,
            "questions": tuple(questions),
        }

    def bundle(self, session_id: int) -> ReportBundle:
        """Prepare the complete session before its connection is closed."""
        data = self.session_data(session_id)
        return ReportBundle(data, data["questions"])
