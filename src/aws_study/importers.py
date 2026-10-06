"""Normalize question-bank JSON and coordinate one atomic import."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .certification_repository import CertificationRepository
from .import_models import ImportedOption, ImportedQuestion
from .import_repository import ImportRepository
from .source_repository import SourceRepository
from .taxonomy import classify


@dataclass
class ImportSummary:
    """Counts describing inserted content and preserved baseline history."""

    questions_seen: int = 0
    questions_inserted: int = 0
    questions_updated: int = 0
    baseline_attempts: int = 0
    exact_duplicate_hashes: int = 0


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _hash(question: str, correct_answers: list[str]) -> str:
    basis = _norm(question) + "\n" + "\n".join(
        sorted(_norm(answer) for answer in correct_answers)
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _source_display_name(source_key: str) -> str:
    return {
        "pretest": "Official Pretest",
        "practice_question_set": "Official Practice Question Set",
        "practice_exam": "Official Practice Exam",
    }.get(source_key, source_key.replace("_", " ").title())


def _options(question: dict[str, Any]) -> tuple[ImportedOption, ...]:
    options = question.get("options") or []
    if not isinstance(options, list) or any(
        not isinstance(option, dict) for option in options
    ):
        raise ValueError(
            "Question options must be an array of option objects."
        )
    return tuple(ImportedOption(
        option.get("letter"), str(option.get("label") or ""),
        bool(option.get("correct")), bool(option.get("selected")),
        option.get("rationale"),
    ) for option in options)


def _baseline_values(
    question: dict[str, Any],
) -> tuple[bool, str | None, int | float | None]:
    """Translate recorded source results rather than regrading old attempts."""
    is_correct = str(question.get("status", "")).lower() == "correct"
    confidence = {
        "Unsure": "low", "Educated guess": "medium", "Confident": "high",
    }.get(question.get("original_confidence"))
    seconds = question.get("time_to_answer_seconds")
    elapsed_ms = seconds * 1000 if seconds is not None else None
    return is_correct, confidence, elapsed_ms


def _question_record(
    question: dict[str, Any],
    options: tuple[ImportedOption, ...],
    *,
    certification_id: int,
    source_id: int,
    fallback_index: int,
    observed_year: int | None,
    verified_year: int | None,
    verification_status: str,
    schema_version: Any,
) -> tuple[ImportedQuestion, list[str]]:
    """Classify and fingerprint content without consulting persistence."""
    text = question["question"]
    topic, concept, tags = classify(text, [o.text for o in options])
    question_type = question.get("question_type") or (
        "multi_select" if sum(o.correct for o in options) > 1
        else "single_select"
    )
    metadata = {
        "original_status": question.get("status"),
        "original_qtype": question.get("qtype"),
        "original_confidence": question.get("original_confidence"),
        "time_to_answer_seconds": question.get("time_to_answer_seconds"),
        "import_schema_version": schema_version,
    }
    record = ImportedQuestion(
        certification_id=certification_id, source_id=source_id,
        source_question_number=question.get("source_index"),
        external_key=str(question.get("source_index") or fallback_index),
        question_text=text, question_type=question_type,
        topic=topic, concept=concept, valid_from_year=observed_year,
        verification_status=verification_status, verified_year=verified_year,
        dedup_group=question.get("dedup_group"),
        dedup_role=question.get("dedup_role") or "canonical",
        variant_group=question.get("variant_group"),
        content_hash=_hash(text, [o.text for o in options if o.correct]),
        metadata_json=json.dumps(metadata),
    )
    return record, tags


def import_internal_bank(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    cert_code: str,
    provider: str = "AWS",
    cert_name: str | None = None,
    observed_year: int | None = None,
    verified_year: int | None = None,
    verification_status: str = "official_current",
    source_type: str = "official",
    import_baseline_attempts: bool = True,
) -> ImportSummary:
    """Import normalized JSON, committing the entire bank or rolling it back.

    Input is a question array or an object with a questions array. Options
    carry letter, answer text in label, correct, and selected. This entry point
    composes repositories sharing the supplied connection; it never closes
    that caller-owned connection.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError(
            "Expected a JSON object containing a 'questions' array, "
            "or a top-level array."
        )

    certifications = CertificationRepository(conn)
    sources = SourceRepository(conn)
    repository = ImportRepository(conn)
    summary = ImportSummary()
    source_ids: dict[str, int] = {}
    baseline_sessions: dict[str, int] = {}
    schema_version = (
        data.get("schema_version") if isinstance(data, dict) else None
    )

    with repository.transaction():
        certification_id = certifications.upsert(
            cert_code, provider=provider, name=cert_name, version=cert_code,
            active_from_year=observed_year,
        )
        for question in questions:
            summary.questions_seen += 1
            if not isinstance(question, dict) or not isinstance(
                question.get("question"), str,
            ):
                raise ValueError(
                    f"Question {summary.questions_seen} "
                    "must have question text."
                )
            source_key = str(question.get("source") or "imported")
            if source_key not in source_ids:
                source_ids[source_key] = sources.upsert(
                    certification_id, source_key,
                    name=_source_display_name(source_key),
                    source_type=source_type, source_file=path.name,
                    observed_year=observed_year,
                    verification_status=verification_status,
                    verified_at=(
                        f"{verified_year}-01-01" if verified_year else None
                    ),
                )
            source_id = source_ids[source_key]
            options = _options(question)
            record, tags = _question_record(
                question, options, certification_id=certification_id,
                source_id=source_id, fallback_index=summary.questions_seen,
                observed_year=observed_year, verified_year=verified_year,
                verification_status=verification_status,
                schema_version=schema_version,
            )
            existing_id = repository.find_question_id(
                source_id, record.external_key,
            )
            if repository.has_duplicate_hash(
                record.content_hash, excluding_id=existing_id,
            ):
                summary.exact_duplicate_hashes += 1
            question_id = repository.save_question(
                record, question_id=existing_id,
            )
            if existing_id is None:
                summary.questions_inserted += 1
            else:
                summary.questions_updated += 1
            option_ids = repository.save_options(question_id, options)
            repository.add_tags(question_id, tags)

            if import_baseline_attempts and any(o.selected for o in options):
                if source_key not in baseline_sessions:
                    session_id = repository.baseline_session(
                        certification_id,
                        label=("Imported baseline: "
                               + _source_display_name(source_key)),
                        target_year=observed_year,
                    )
                    baseline_sessions[source_key] = session_id
                is_correct, confidence, elapsed_ms = _baseline_values(question)
                inserted = repository.save_baseline_attempt(
                    baseline_sessions[source_key], question_id,
                    position=int(record.external_key),
                    selected=[option_id for option_id, option in
                              zip(option_ids, options) if option.selected],
                    is_correct=is_correct, confidence=confidence,
                    elapsed_ms=elapsed_ms,
                )
                summary.baseline_attempts += int(inserted)
    return summary
