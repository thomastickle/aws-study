"""Non-destructive v1 to v2 conversion with explicit history mapping."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from .bank_schema import validate_question
from .db import init_db, open_readonly, schema_version
from .fingerprints import normalize_match_text
from .import_repository import ImportRepository
from .migration_repository import HISTORY_TABLES, MigrationRepository

SOURCE_KEYS_2026 = {
    "pretest": "official-pretest-2026",
    "practice_question_set": "official-practice-question-set-2026",
    "practice_exam": "official-practice-exam-2026",
}


def _migrate_records(
    conn: sqlite3.Connection, records: dict
) -> dict[str, int]:
    repository = MigrationRepository(conn)
    imports = ImportRepository(conn)
    options = defaultdict(list)
    for option in records["options"]:
        options[option["question_id"]].append(option)
    for items in options.values():
        items.sort(key=lambda o: o["option_order"])
    question_map, answer_map = {}, {}
    source_orders = defaultdict(int)
    source_refs = set()
    active = defaultdict(int)
    with repository.transaction():
        for cert in records["certifications"]:
            repository.insert_record("certifications", cert)
        for source in records["sources"]:
            source = dict(source)
            if source["observed_year"] == 2026:
                source["source_key"] = SOURCE_KEYS_2026.get(
                    source["source_key"], source["source_key"]
                )
            # V1 did not record who set verification. Preserve its effective state.
            source["verification_origin"] = "manual"
            repository.insert_record("sources", source)
        for old in sorted(records["questions"], key=lambda q: q["id"]):
            choices = options[old["id"]]
            ref = old["source_question_number"]
            if ref is None:
                ref = old["external_key"]
            try:
                question = validate_question(
                    {
                        "question": old["question_text"],
                        "type": old["question_type"],
                        "select_count": sum(o["is_correct"] for o in choices),
                        "source_ref": str(ref) if ref is not None else None,
                        "answers": [
                            {
                                "text": o["option_text"],
                                "correct": bool(o["is_correct"]),
                                "rationale": o["rationale"],
                            }
                            for o in choices
                        ],
                    },
                    curated=False,
                )
            except ValueError as error:
                raise ValueError(
                    f"Legacy question {old['id']}: {error}"
                ) from error
            if question.source_ref is not None:
                occurrence = (old["source_id"], question.source_ref)
                if occurrence in source_refs:
                    raise ValueError(
                        f"Conflicting legacy source reference: {occurrence!r}"
                    )
                source_refs.add(occurrence)
            qid, _ = imports.canonical_question(
                old["certification_id"],
                question,
                context=f"legacy question {old['id']}, source {old['source_id']}, ref {ref!r}",
                preferred_id=old["id"],
                is_active=old["is_active"],
                created_at=old["created_at"],
            )
            question_map[old["id"]] = qid
            active[qid] = max(active[qid], old["is_active"])
            ids = imports.answer_ids(qid)
            for option in choices:
                answer_map[option["id"]] = ids[
                    normalize_match_text(option["option_text"])
                ]
            source_orders[old["source_id"]] += 1
            imports.provenance(
                qid,
                old["source_id"],
                question,
                order=source_orders[old["source_id"]],
                observed_year=old["valid_from_year"],
                valid_to_year=old["valid_to_year"],
                status=old["verification_status"],
                verified_year=old["verified_year"],
                created_at=old["created_at"],
            )
        repository.set_active(active)
        seen_session_questions = set()
        for table in (
            "sessions",
            "session_questions",
            "attempts",
            "attempt_options",
            "review_notes",
        ):
            for old in records[table]:
                row = dict(old)
                if row.get("question_id") is not None:
                    row["question_id"] = question_map[row["question_id"]]
                if table == "session_questions":
                    key = (row["session_id"], row["question_id"])
                    if key in seen_session_questions:
                        raise ValueError(
                            f"Migration would collapse two questions in session {key[0]} "
                            f"to canonical question {key[1]}; legacy question {old['question_id']}"
                        )
                    seen_session_questions.add(key)
                if table == "attempt_options":
                    row["option_id"] = answer_map[row["option_id"]]
                repository.insert_record(table, row)
        expected = {table: len(records[table]) for table in HISTORY_TABLES}
        summary = repository.validate(expected)
        if summary["provenance_rows"] != len(records["questions"]):
            raise ValueError(
                "Migration did not preserve every source occurrence"
            )
    return {
        "questions_read": len(records["questions"]),
        **summary,
        "certifications_preserved": len(records["certifications"]),
        "sessions_preserved": len(records["sessions"]),
        "session_questions_preserved": len(records["session_questions"]),
        "attempts_preserved": len(records["attempts"]),
        "selected_answer_records_preserved": len(records["attempt_options"]),
        "review_notes_preserved": len(records["review_notes"]),
        "conflicts": 0,
    }


def migrate_v1_to_v2(source: str | Path, dest: str | Path) -> dict[str, int]:
    """Build and verify a separate database; publish only after success.

    The destination must not exist. Hard-link publication refuses to overwrite
    a destination created concurrently, and the source is opened read-only.
    """
    source, dest = Path(source), Path(dest)
    if dest.exists() or dest.is_symlink():
        raise ValueError(f"Destination already exists: {dest}")
    with closing(open_readonly(source)) as original:
        if schema_version(original) != 1:
            raise ValueError("Migration requires a recognized v1 database")
        records = MigrationRepository(original).snapshot()
    dest.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".aws-study-migration-", suffix=".db", dir=dest.parent
    )
    os.close(descriptor)
    temporary = Path(temp_name)
    try:
        with closing(sqlite3.connect(temporary)) as conn:
            conn.row_factory = sqlite3.Row
            init_db(conn)
            summary = _migrate_records(conn, records)
        os.link(temporary, dest)
        return summary
    finally:
        temporary.unlink(missing_ok=True)
