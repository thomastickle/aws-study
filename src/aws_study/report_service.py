"""Assemble full session context and attempted-question review records."""

from __future__ import annotations

from .report_models import (
    JsonRecord,
    ReportBundle,
    ReportSessionQuestion,
    SessionData,
)
from .report_repository import ReportRepository


def _choice_text(answer: JsonRecord) -> str:
    return f"{answer['label']}. {answer['answer_text']}"


def _context_source(
    source: JsonRecord, answers: dict[int, JsonRecord]
) -> JsonRecord:
    """Keep provenance and source ordering, inheriting identical rationales."""
    context = {
        key: source[key]
        for key in (
            "source_key",
            "name",
            "source_ref",
            "source_order",
            "source_type",
            "observed_year",
            "verification_status",
            "verified_year",
            "verification_origin",
            "valid_from_year",
            "valid_to_year",
        )
    }
    references = []
    for answer in source["answers"]:
        reference = {
            "answer_id": answer["id"],
            "source_order": answer["source_order"],
        }
        # Presence matters: null and empty overrides must not inherit text.
        if answer["rationale"] != answers[answer["id"]]["rationale"]:
            reference["rationale"] = answer["rationale"]
        references.append(reference)
    context["answers"] = references
    return context


def _context_question(record: ReportSessionQuestion) -> JsonRecord:
    """Build a self-contained question with one full copy of each answer."""
    question = {
        key: record.details[key]
        for key in (
            "id",
            "question_text",
            "question_type",
            "select_count",
            "area",
            "topic",
            "selection_group",
            "position",
            "flagged",
        )
    }
    answers = [
        dict(answer, is_correct=bool(answer["is_correct"]))
        for answer in record.answers
    ]
    by_id = {answer["id"]: answer for answer in answers}
    question["answers"] = answers
    question["selected_answer_ids"] = [
        answer["id"]
        for answer in answers
        if answer["id"] in record.selected_answer_ids
    ]
    question["correct_answer_ids"] = [
        answer["id"] for answer in answers if answer["is_correct"]
    ]
    question["sources"] = [
        _context_source(source, by_id) for source in record.details["sources"]
    ]
    question["result"] = None
    if record.attempt is not None:
        question["result"] = {
            "is_correct": bool(record.attempt["is_correct"]),
            "confidence": record.attempt["confidence"],
            "elapsed_ms": record.attempt["elapsed_ms"],
        }
    return question


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
            question = _context_question(record)
            if record.attempt is not None:
                attempt = dict(record.attempt)
                for key in ("question_text", "area", "topic", "sources"):
                    attempt[key] = question[key]
                attempt["selected"] = [
                    _choice_text(answer)
                    for answer in record.answers
                    if answer["id"] in record.selected_answer_ids
                ]
                attempt["correct"] = [
                    _choice_text(answer)
                    for answer in record.answers
                    if answer["is_correct"]
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
