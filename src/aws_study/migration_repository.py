"""Read legacy records and validate/copy history into the current canonical schema."""

from __future__ import annotations

from .repository import SQLiteRepository

TABLES = (
    "certifications",
    "sources",
    "questions",
    "options",
    "sessions",
    "session_questions",
    "attempts",
    "attempt_options",
    "review_notes",
)
HISTORY_TABLES = (
    "certifications",
    "sessions",
    "session_questions",
    "attempts",
    "attempt_options",
    "review_notes",
)


class MigrationRepository(SQLiteRepository):
    """Migration-only persistence; SQL identifiers come from fixed table sets."""

    def snapshot(self) -> dict[str, list[dict]]:
        """Read all legacy records from one consistent source transaction."""
        self._conn.execute("BEGIN")
        try:
            if self._conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Legacy database has foreign-key violations")
            return {
                table: [
                    dict(r)
                    for r in self._conn.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    )
                ]
                for table in TABLES
            }
        finally:
            self._conn.rollback()

    def insert_record(self, table: str, record: dict) -> None:
        """Preserve complete metadata with named binds and whitelisted columns."""
        if table not in TABLES or table in ("questions", "options"):
            raise ValueError(f"Unsupported copy table {table}")
        allowed = {
            r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")
        }
        if not record.keys() <= allowed:
            raise ValueError(f"Unknown legacy columns in {table}")
        columns = ",".join(record)
        binds = ",".join(f":{column}" for column in record)
        self._conn.execute(
            f"INSERT INTO {table}({columns}) VALUES ({binds})", record
        )

    def validate(self, expected_counts: dict[str, int]) -> dict[str, int]:
        """Check relationships, grading, cardinality, and all history counts."""
        foreign_keys = self._conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_keys:
            raise ValueError(
                f"Destination foreign-key violations: {foreign_keys!r}"
            )
        if self._conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Destination integrity check failed")
        for table, expected in expected_counts.items():
            if table not in HISTORY_TABLES:
                raise ValueError(f"Unknown validation table {table}")
            actual = self._conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            if actual != expected:
                raise ValueError(
                    f"{table}: expected {expected}, found {actual}"
                )
        checks = {
            "incomplete session answer order": """
                SELECT sq.question_id FROM session_questions sq
                WHERE (SELECT COUNT(*) FROM session_answers sa WHERE
                       sa.session_id=sq.session_id AND sa.question_id=sq.question_id)
                      <> (SELECT COUNT(*) FROM answers ans WHERE ans.question_id=sq.question_id)""",
            "selected answer belongs to another question": """
                SELECT ao.attempt_id FROM attempt_options ao
                JOIN attempts a ON a.id=ao.attempt_id
                JOIN answers ans ON ans.id=ao.option_id
                WHERE ans.question_id<>a.question_id""",
            "attempt is outside its session/certification": """
                SELECT a.id FROM attempts a JOIN sessions s ON s.id=a.session_id
                JOIN questions q ON q.id=a.question_id WHERE
                s.certification_id<>q.certification_id OR NOT EXISTS (
                    SELECT 1 FROM session_questions sq WHERE
                    sq.session_id=a.session_id AND sq.question_id=a.question_id)""",
            "session question belongs to another certification": """
                SELECT sq.question_id FROM session_questions sq
                JOIN sessions s ON s.id=sq.session_id
                JOIN questions q ON q.id=sq.question_id
                WHERE s.certification_id<>q.certification_id""",
            "invalid question answer count": """
                SELECT q.id FROM questions q LEFT JOIN answers a ON a.question_id=q.id
                GROUP BY q.id HAVING COUNT(a.id)<2 OR SUM(a.is_correct)<>q.select_count""",
            "provenance belongs to another certification": """
                SELECT qs.id FROM question_sources qs JOIN questions q ON q.id=qs.question_id
                JOIN sources s ON s.id=qs.source_id
                WHERE q.certification_id<>s.certification_id""",
            "question has no provenance": """
                SELECT q.id FROM questions q WHERE NOT EXISTS (
                    SELECT 1 FROM question_sources qs WHERE qs.question_id=q.id)""",
            "source answer belongs to another question": """
                SELECT qsa.question_source_id FROM question_source_answers qsa
                JOIN question_sources qs ON qs.id=qsa.question_source_id
                JOIN answers a ON a.id=qsa.answer_id WHERE a.question_id<>qs.question_id""",
            "review note belongs to another certification": """
                SELECT n.id FROM review_notes n JOIN questions q ON q.id=n.question_id
                WHERE n.certification_id<>q.certification_id""",
            "stored attempt correctness mismatch": """
                SELECT a.id FROM attempts a WHERE a.is_correct <> (
                    NOT EXISTS (
                        SELECT 1 FROM answers ans WHERE ans.question_id=a.question_id
                        AND ans.is_correct <> EXISTS (
                            SELECT 1 FROM attempt_options ao WHERE ao.attempt_id=a.id
                            AND ao.option_id=ans.id AND ao.selected=1)))""",
        }
        for label, sql in checks.items():
            failures = [r[0] for r in self._conn.execute(sql)]
            if failures:
                raise ValueError(
                    f"Migration failure: {label}; record IDs={failures}"
                )
        return {
            "canonical_questions": self._conn.execute(
                "SELECT COUNT(*) FROM questions"
            ).fetchone()[0],
            "provenance_rows": self._conn.execute(
                "SELECT COUNT(*) FROM question_sources"
            ).fetchone()[0],
            "foreign_key_violations": 0,
        }

    def set_active(self, states: dict[int, int]) -> None:
        """A merged question remains active if any legacy instance was active."""
        self._conn.executemany(
            "UPDATE questions SET is_active=:active WHERE id=:id",
            ({"id": qid, "active": active} for qid, active in states.items()),
        )
