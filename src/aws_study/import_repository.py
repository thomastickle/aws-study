"""Persistence for imported questions, taxonomy, and historical baselines."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict

from .import_models import ImportedOption, ImportedQuestion
from .repository import SQLiteRepository


class ImportRepository(SQLiteRepository):
    """Save normalized import records without parsing JSON or committing."""

    def find_question_id(
        self, source_id: int, external_key: str,
    ) -> int | None:
        """Find a question by its stable identity within a source."""
        row = self._conn.execute(
            """SELECT id FROM questions
               WHERE source_id=:source_id AND external_key=:external_key""",
            {"source_id": source_id, "external_key": external_key},
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def has_duplicate_hash(
        self, content_hash: str, *, excluding_id: int | None,
    ) -> bool:
        """Check for another record with identical normalized content."""
        return self._conn.execute(
            """SELECT 1 FROM questions WHERE content_hash=:content_hash
               AND (:excluding_id IS NULL OR id<>:excluding_id) LIMIT 1""",
            {"content_hash": content_hash, "excluding_id": excluding_id},
        ).fetchone() is not None

    def save_question(
        self, question: ImportedQuestion, *, question_id: int | None,
    ) -> int:
        """Insert or update content while preserving identity and history."""
        params = asdict(question)
        if question_id is not None:
            params["question_id"] = question_id
            self._conn.execute(
                """UPDATE questions SET
                       question_text=:question_text,
                       question_type=:question_type, topic=:topic,
                       concept=:concept, verified_year=:verified_year,
                       verification_status=:verification_status,
                       dedup_group=:dedup_group, dedup_role=:dedup_role,
                       variant_group=:variant_group,
                       content_hash=:content_hash, metadata_json=:metadata_json
                   WHERE id=:question_id""",
                params,
            )
            return question_id
        cursor = self._conn.execute(
            """INSERT INTO questions(
                   certification_id, source_id, source_question_number,
                   external_key, question_text, question_type, topic, concept,
                   valid_from_year, verification_status, verified_year,
                   dedup_group, dedup_role, variant_group, content_hash,
                   metadata_json)
               VALUES (:certification_id, :source_id, :source_question_number,
                       :external_key, :question_text, :question_type, :topic,
                       :concept, :valid_from_year, :verification_status,
                       :verified_year, :dedup_group, :dedup_role,
                       :variant_group, :content_hash, :metadata_json)""",
            params,
        )
        return int(cursor.lastrowid)

    def save_options(
        self, question_id: int, options: Sequence[ImportedOption],
    ) -> tuple[int, ...]:
        """Upsert choices by position and return their IDs in input order.

        Existing option IDs remain stable because attempts reference them.
        As with question updates, importing never deletes historical records.
        """
        option_ids = []
        for position, option in enumerate(options):
            params = {
                "question_id": question_id, "position": position,
                "label": option.label, "text": option.text,
                "correct": int(option.correct), "rationale": option.rationale,
            }
            self._conn.execute(
                """INSERT INTO options(
                       question_id, option_order, option_label, option_text,
                       is_correct, rationale)
                   VALUES (:question_id, :position, :label, :text,
                           :correct, :rationale)
                   ON CONFLICT(question_id, option_order) DO UPDATE SET
                       option_label=excluded.option_label,
                       option_text=excluded.option_text,
                       is_correct=excluded.is_correct,
                       rationale=excluded.rationale""",
                params,
            )
            row = self._conn.execute(
                """SELECT id FROM options
                   WHERE question_id=:question_id
                     AND option_order=:position""",
                params,
            ).fetchone()
            option_ids.append(int(row["id"]))
        return tuple(option_ids)

    def add_tags(self, question_id: int, tags: Sequence[str]) -> None:
        """Attach classified tags idempotently without erasing prior tags."""
        for tag in tags:
            params = {"tag": tag, "question_id": question_id}
            self._conn.execute(
                "INSERT OR IGNORE INTO tags(name) VALUES (:tag)", params,
            )
            self._conn.execute(
                """INSERT OR IGNORE INTO question_tags(question_id, tag_id)
                   SELECT :question_id, id FROM tags WHERE name=:tag""",
                params,
            )

    def baseline_session(
        self, certification_id: int, *, label: str, target_year: int | None,
    ) -> int:
        """Find or create the import's historical assessment session."""
        params = {
            "certification_id": certification_id, "label": label,
            "target_year": target_year,
        }
        row = self._conn.execute(
            """SELECT id FROM sessions
               WHERE certification_id=:certification_id
                 AND source_kind='imported_baseline'
                 AND session_label=:label""",
            params,
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = self._conn.execute(
            """INSERT INTO sessions(
                   certification_id, target_year, mode, strategy,
                   session_label, source_kind)
               VALUES (:certification_id, :target_year, 'exam', 'source_order',
                       :label, 'imported_baseline')""",
            params,
        )
        return int(cursor.lastrowid)

    def save_baseline_attempt(
        self,
        session_id: int,
        question_id: int,
        *,
        position: int,
        selected: Sequence[int],
        is_correct: bool,
        confidence: str | None,
        elapsed_ms: int | float | None,
    ) -> bool:
        """Save a historical answer once; return whether it was inserted."""
        params = {
            "session_id": session_id, "question_id": question_id,
            "position": position, "is_correct": int(is_correct),
            "confidence": confidence, "elapsed_ms": elapsed_ms,
        }
        self._conn.execute(
            """INSERT OR IGNORE INTO session_questions(
                   session_id, question_id, position)
               VALUES (:session_id, :question_id, :position)""",
            params,
        )
        prior = self._conn.execute(
            """SELECT id FROM attempts
               WHERE session_id=:session_id AND question_id=:question_id
                 AND source_kind='imported_baseline'""",
            params,
        ).fetchone()
        if prior is not None:
            return False
        cursor = self._conn.execute(
            """INSERT INTO attempts(
                   session_id, question_id, is_correct, confidence,
                   elapsed_ms, source_kind)
               VALUES (:session_id, :question_id, :is_correct, :confidence,
                       :elapsed_ms, 'imported_baseline')""",
            params,
        )
        self._conn.executemany(
            """INSERT INTO attempt_options(attempt_id, option_id, selected)
               VALUES (:attempt_id, :option_id, 1)""",
            [{"attempt_id": cursor.lastrowid, "option_id": option_id}
             for option_id in selected],
        )
        return True
