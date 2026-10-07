"""Assemble typed session context and attempted-question review records."""

from __future__ import annotations

from .report_models import (
    ContextAnswer,
    ContextQuestion,
    ContextSession,
    ContextSource,
    ContextSourceAnswer,
    JsonRecord,
    PreparedAttempt,
    ReportSessionQuestion,
    SessionData,
)
from .report_repository import ReportRepository


def _choice_text(answer: ContextAnswer) -> str:
    return f"{answer['label']}. {answer['answer_text']}"


def _context_source(
    source: JsonRecord, answers: dict[int, ContextAnswer]
) -> ContextSource:
    """Keep provenance and source ordering, inheriting identical rationales."""
    references: list[ContextSourceAnswer] = []
    for answer in source["answers"]:
        reference: ContextSourceAnswer = {
            "answer_id": answer["id"],
            "source_order": answer["source_order"],
        }
        # Presence matters: null and empty overrides must not inherit text.
        if answer["rationale"] != answers[answer["id"]]["rationale"]:
            reference["rationale"] = answer["rationale"]
        references.append(reference)
    return {
        "source_key": source["source_key"],
        "name": source["name"],
        "source_ref": source["source_ref"],
        "source_order": source["source_order"],
        "source_type": source["source_type"],
        "observed_year": source["observed_year"],
        "verification_status": source["verification_status"],
        "verified_year": source["verified_year"],
        "verification_origin": source["verification_origin"],
        "valid_from_year": source["valid_from_year"],
        "valid_to_year": source["valid_to_year"],
        "answers": references,
    }


def _context_question(record: ReportSessionQuestion) -> ContextQuestion:
    """Build a self-contained question with one full copy of each answer."""
    details = record.details
    answers: list[ContextAnswer] = [
        {
            "id": answer["id"],
            "display_order": answer["display_order"],
            "label": answer["label"],
            "answer_text": answer["answer_text"],
            "is_correct": bool(answer["is_correct"]),
            "rationale": answer["rationale"],
        }
        for answer in record.answers
    ]
    by_id = {answer["id"]: answer for answer in answers}
    return {
        "id": details["id"],
        "question_text": details["question_text"],
        "question_type": details["question_type"],
        "select_count": details["select_count"],
        "area": details["area"],
        "topic": details["topic"],
        "selection_group": details["selection_group"],
        "position": details["position"],
        "flagged": details["flagged"],
        "answers": answers,
        "selected_answer_ids": [
            answer["id"]
            for answer in answers
            if answer["id"] in record.selected_answer_ids
        ],
        "correct_answer_ids": [
            answer["id"] for answer in answers if answer["is_correct"]
        ],
        "sources": [
            _context_source(source, by_id) for source in details["sources"]
        ],
        "result": None
        if record.attempt is None
        else {
            "is_correct": bool(record.attempt["is_correct"]),
            "confidence": record.attempt["confidence"],
            "elapsed_ms": record.attempt["elapsed_ms"],
        },
    }


def _context_session(session: JsonRecord) -> ContextSession:
    """Make the retained session fields explicit at the prepared boundary."""
    return {
        "id": session["id"],
        "certification_id": session["certification_id"],
        "started_at": session["started_at"],
        "completed_at": session["completed_at"],
        "target_year": session["target_year"],
        "mode": session["mode"],
        "strategy": session["strategy"],
        "requested_count": session["requested_count"],
        "session_label": session["session_label"],
        "source_kind": session["source_kind"],
        "notes": session["notes"],
        "provider": session["provider"],
        "code": session["code"],
        "cert_name": session["cert_name"],
    }


class ReportService:
    """Build presentation inputs without terminal or filesystem I/O."""

    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def resolve_session(self, value: str) -> int:
        """Accept a numeric ID or the newest reportable interactive session."""
        return (
            self._repository.latest_reportable_interactive_session()
            if value == "latest"
            else int(value)
        )

    def session_data(self, session_id: int) -> SessionData:
        """Prepare all exports before the caller closes its database connection."""
        session = _context_session(self._repository.session(session_id))
        if (
            session["mode"] == "exam"
            and session["source_kind"] == "interactive"
            and session["completed_at"] is None
        ):
            raise ValueError(
                f"Draft exam {session_id} is not submitted. "
                f"Resume with quiz --resume {session_id}."
            )
        questions: list[ContextQuestion] = []
        attempts: list[PreparedAttempt] = []
        for record in self._repository.session_questions(session_id):
            question = _context_question(record)
            if record.attempt is not None:
                attempt = record.attempt
                attempts.append(
                    {
                        "id": attempt["id"],
                        "session_id": attempt["session_id"],
                        "question_id": attempt["question_id"],
                        "attempted_at": attempt["attempted_at"],
                        "is_correct": bool(attempt["is_correct"]),
                        "confidence": attempt["confidence"],
                        "elapsed_ms": attempt["elapsed_ms"],
                        "source_kind": attempt["source_kind"],
                        "note": attempt["note"],
                        "question_text": question["question_text"],
                        "area": question["area"],
                        "topic": question["topic"],
                        "sources": question["sources"],
                        "selected": [
                            _choice_text(answer)
                            for answer in question["answers"]
                            if answer["id"] in record.selected_answer_ids
                        ],
                        "correct": [
                            _choice_text(answer)
                            for answer in question["answers"]
                            if answer["is_correct"]
                        ],
                    }
                )
            questions.append(question)
        return {
            "session": session,
            "attempts": attempts,
            "questions": tuple(questions),
        }
