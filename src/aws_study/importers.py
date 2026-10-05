from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import get_or_create_cert
from .taxonomy import classify


@dataclass
class ImportSummary:
    questions_seen: int = 0
    questions_inserted: int = 0
    questions_updated: int = 0
    baseline_attempts: int = 0
    exact_duplicate_hashes: int = 0


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _hash(question: str, correct_answers: list[str]) -> str:
    basis = _norm(question) + "\n" + "\n".join(sorted(_norm(x) for x in correct_answers))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _source_display_name(source_key: str) -> str:
    return {
        "pretest": "Official Pretest",
        "practice_question_set": "Official Practice Question Set",
        "practice_exam": "Official Practice Exam",
    }.get(source_key, source_key.replace("_", " ").title())


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
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(questions, list):
        raise ValueError("Expected a JSON object containing a 'questions' array, or a top-level array.")

    cert = get_or_create_cert(
        conn,
        cert_code,
        provider=provider,
        name=cert_name,
        version=cert_code,
        active_from_year=observed_year,
    )
    summary = ImportSummary()
    source_ids: dict[str, int] = {}
    baseline_sessions: dict[str, int] = {}

    for q in questions:
        summary.questions_seen += 1
        source_key = str(q.get("source") or "imported")
        if source_key not in source_ids:
            row = conn.execute(
                "SELECT id FROM sources WHERE certification_id=? AND source_key=?",
                (cert, source_key),
            ).fetchone()
            if row:
                sid = int(row["id"])
                conn.execute(
                    """
                    UPDATE sources SET observed_year=COALESCE(?, observed_year),
                      verification_status=?, verified_at=COALESCE(?, verified_at), source_type=?
                    WHERE id=?
                    """,
                    (
                        observed_year,
                        verification_status,
                        f"{verified_year}-01-01" if verified_year else None,
                        source_type,
                        sid,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO sources(certification_id, source_key, name, source_type, source_file,
                                        observed_year, verification_status, verified_at)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        cert,
                        source_key,
                        _source_display_name(source_key),
                        source_type,
                        Path(path).name,
                        observed_year,
                        verification_status,
                        f"{verified_year}-01-01" if verified_year else None,
                    ),
                )
                sid = int(cur.lastrowid)
            source_ids[source_key] = sid

        sid = source_ids[source_key]
        options = q.get("options") or []
        correct = [str(o.get("label") or "") for o in options if o.get("correct")]
        content_hash = _hash(str(q["question"]), correct)

        topic, concept, tags = classify(
            str(q["question"]), [str(o.get("label") or "") for o in options]
        )
        external_key = str(q.get("source_index") or summary.questions_seen)
        question_type = q.get("question_type") or (
            "multi_select" if sum(1 for o in options if o.get("correct")) > 1 else "single_select"
        )
        metadata = {
            "original_status": q.get("status"),
            "original_qtype": q.get("qtype"),
            "original_confidence": q.get("original_confidence"),
            "time_to_answer_seconds": q.get("time_to_answer_seconds"),
            "import_schema_version": data.get("schema_version") if isinstance(data, dict) else None,
        }

        existing = conn.execute(
            "SELECT id FROM questions WHERE source_id=? AND external_key=?", (sid, external_key)
        ).fetchone()
        if existing:
            prior_hash_count = conn.execute(
                "SELECT COUNT(*) AS n FROM questions WHERE content_hash=? AND id<>?",
                (content_hash, existing["id"]),
            ).fetchone()["n"]
        else:
            prior_hash_count = conn.execute(
                "SELECT COUNT(*) AS n FROM questions WHERE content_hash=?", (content_hash,)
            ).fetchone()["n"]
        if prior_hash_count:
            summary.exact_duplicate_hashes += 1

        if existing:
            qid = int(existing["id"])
            conn.execute(
                """
                UPDATE questions SET question_text=?, question_type=?, topic=?, concept=?,
                    verified_year=?, verification_status=?, dedup_group=?, dedup_role=?,
                    variant_group=?, content_hash=?, metadata_json=?
                WHERE id=?
                """,
                (
                    q["question"], question_type, topic, concept, verified_year,
                    verification_status, q.get("dedup_group"), q.get("dedup_role") or "canonical",
                    q.get("variant_group"), content_hash, json.dumps(metadata), qid,
                ),
            )
            summary.questions_updated += 1
        else:
            cur = conn.execute(
                """
                INSERT INTO questions(certification_id, source_id, source_question_number, external_key,
                    question_text, question_type, topic, concept, valid_from_year,
                    verification_status, verified_year, dedup_group, dedup_role, variant_group,
                    content_hash, metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    cert, sid, q.get("source_index"), external_key, q["question"], question_type,
                    topic, concept, observed_year, verification_status, verified_year,
                    q.get("dedup_group"), q.get("dedup_role") or "canonical", q.get("variant_group"),
                    content_hash, json.dumps(metadata),
                ),
            )
            qid = int(cur.lastrowid)
            summary.questions_inserted += 1

        option_id_by_index: dict[int, int] = {}
        for idx, o in enumerate(options):
            conn.execute(
                """
                INSERT INTO options(question_id, option_order, option_label, option_text, is_correct, rationale)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(question_id, option_order) DO UPDATE SET
                    option_label=excluded.option_label, option_text=excluded.option_text,
                    is_correct=excluded.is_correct, rationale=excluded.rationale
                """,
                (qid, idx, o.get("letter"), o.get("label") or "", 1 if o.get("correct") else 0, o.get("rationale")),
            )
            option_id_by_index[idx] = int(conn.execute(
                "SELECT id FROM options WHERE question_id=? AND option_order=?", (qid, idx)
            ).fetchone()["id"])

        for tag in tags:
            conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO question_tags(question_id, tag_id) VALUES (?,?)",
                (qid, tag_id),
            )

        # Import the user's recorded answer from the source summary as a historical baseline.
        if import_baseline_attempts and any(o.get("selected") for o in options):
            if source_key not in baseline_sessions:
                row = conn.execute(
                    """
                    SELECT id FROM sessions WHERE certification_id=? AND source_kind='imported_baseline' AND session_label=?
                    """,
                    (cert, f"Imported baseline: {_source_display_name(source_key)}"),
                ).fetchone()
                if row:
                    session_id = int(row["id"])
                else:
                    cur = conn.execute(
                        """
                        INSERT INTO sessions(certification_id, target_year, mode, strategy, session_label, source_kind)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (cert, observed_year, "exam", "source_order", f"Imported baseline: {_source_display_name(source_key)}", "imported_baseline"),
                    )
                    session_id = int(cur.lastrowid)
                baseline_sessions[source_key] = session_id
            session_id = baseline_sessions[source_key]
            pos = int(q.get("source_index") or summary.questions_seen)
            conn.execute(
                "INSERT OR IGNORE INTO session_questions(session_id, question_id, position) VALUES (?,?,?)",
                (session_id, qid, pos),
            )
            prior_attempt = conn.execute(
                "SELECT id FROM attempts WHERE session_id=? AND question_id=? AND source_kind='imported_baseline'",
                (session_id, qid),
            ).fetchone()
            if not prior_attempt:
                is_correct = 1 if str(q.get("status", "")).lower() == "correct" else 0
                confidence = {
                    "Unsure": "low",
                    "Educated guess": "medium",
                    "Confident": "high",
                }.get(q.get("original_confidence"))
                elapsed_ms = (q.get("time_to_answer_seconds") * 1000) if q.get("time_to_answer_seconds") is not None else None
                cur = conn.execute(
                    """
                    INSERT INTO attempts(session_id, question_id, is_correct, confidence, elapsed_ms, source_kind)
                    VALUES (?,?,?,?,?, 'imported_baseline')
                    """,
                    (session_id, qid, is_correct, confidence, elapsed_ms),
                )
                aid = int(cur.lastrowid)
                for idx, o in enumerate(options):
                    if o.get("selected"):
                        conn.execute(
                            "INSERT INTO attempt_options(attempt_id, option_id, selected) VALUES (?,?,1)",
                            (aid, option_id_by_index[idx]),
                        )
                summary.baseline_attempts += 1

    conn.commit()
    return summary
