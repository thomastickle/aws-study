"""SQLite persistence for quiz sessions, answers, and selection history."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from .fingerprints import normalize_match_text
from .quiz_models import AttemptHistory, Option, Question, QuestionHistory
from .repository import SQLiteRepository


class QuizRepository(SQLiteRepository):
    """Map stored quiz data to models; the caller controls transactions."""

    def candidate_history(
        self,
        cert_id: int,
        *,
        target_year: int | None,
        include_unverified: bool = False,
        include_recent: bool = True,
    ) -> tuple[QuestionHistory, ...]:
        """Load eligible questions and the history needed for weighting."""
        where = ["q.certification_id=:certification_id", "q.is_active=1"]
        params = {"certification_id": cert_id, "target_year": target_year}
        provenance = ["qs.question_id=q.id"]
        if target_year is not None:
            provenance.extend(
                [
                    "(qs.valid_from_year IS NULL OR qs.valid_from_year<=:target_year)",
                    "(qs.valid_to_year IS NULL OR qs.valid_to_year>=:target_year)",
                ]
            )
        if not include_unverified:
            provenance.append(
                "COALESCE(qs.verification_status, 'unverified') "
                "IN ('official_current','verified_current')"
            )
            if target_year is not None:
                provenance.append("qs.verified_year>=:target_year")
        where.append(
            "EXISTS (SELECT 1 FROM question_sources qs WHERE "
            + " AND ".join(provenance)
            + ")"
        )
        rows = self._conn.execute(
            f"""SELECT q.id, q.question_text, g.group_key, COUNT(a.id) AS attempts,
                       SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END)
                       AS misses
                FROM questions q LEFT JOIN attempts a ON a.question_id=q.id
                LEFT JOIN question_selection_groups g ON g.question_id=q.id
                WHERE {' AND '.join(where)} GROUP BY q.id""",
            params,
        ).fetchall()
        return tuple(
            QuestionHistory(
                question_id=row["id"],
                attempt_count=row["attempts"],
                miss_count=row["misses"] or 0,
                recent_attempts=(
                    self._recent_attempts(row["id"])
                    if include_recent and row["attempts"]
                    else ()
                ),
                selection_group=row["group_key"],
                normalized_stem=normalize_match_text(row["question_text"]),
            )
            for row in rows
        )

    def _recent_attempts(self, question_id: int) -> tuple[AttemptHistory, ...]:
        return tuple(
            AttemptHistory(
                bool(attempt["is_correct"]),
                attempt["confidence"],
                attempt["attempted_at"],
            )
            for attempt in self._conn.execute(
                """SELECT is_correct, confidence, attempted_at
                   FROM attempts WHERE question_id=:question_id
                   ORDER BY id DESC LIMIT 5""",
                {"question_id": question_id},
            )
        )

    def insert_session(
        self,
        cert_id: int,
        *,
        started_at: str,
        target_year: int | None,
        mode: str,
        strategy: str,
        requested_count: int,
        selected_questions: Sequence[tuple[int, float]],
    ) -> int:
        """Insert a session and its ordered (question ID, weight) pairs."""
        cursor = self._conn.execute(
            """INSERT INTO sessions(
                   certification_id, started_at, target_year, mode, strategy,
                   requested_count, source_kind)
               VALUES (:certification_id, :started_at, :target_year, :mode,
                       :strategy, :requested_count, 'interactive')""",
            {
                "certification_id": cert_id,
                "started_at": started_at,
                "target_year": target_year,
                "mode": mode,
                "strategy": strategy,
                "requested_count": requested_count,
            },
        )
        session_id = int(cursor.lastrowid)
        for position, (question_id, weight) in enumerate(
            selected_questions,
            1,
        ):
            self._conn.execute(
                """INSERT INTO session_questions(
                       session_id, question_id, position, selection_weight)
                   VALUES (:session_id, :question_id, :position, :weight)""",
                {
                    "session_id": session_id,
                    "question_id": question_id,
                    "position": position,
                    "weight": weight,
                },
            )
        return session_id

    def lock_session(self, session_id: int) -> None:
        """Acquire a write lock before checking session state."""
        self._conn.execute(
            "UPDATE sessions SET mode=mode WHERE id=:session_id",
            {"session_id": session_id},
        )

    def is_complete(self, session_id: int) -> bool:
        """Return completion state; unknown sessions raise ValueError."""
        row = self._conn.execute(
            "SELECT completed_at FROM sessions WHERE id=:session_id",
            {"session_id": session_id},
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown session {session_id}")
        return row["completed_at"] is not None

    def questions(self, session_id: int) -> tuple[Question, ...]:
        """Load a session's questions and choices in their saved order."""
        return tuple(
            self._question(row)
            for row in self._conn.execute(
                """SELECT q.id, q.question_text, q.question_type, q.select_count
                   FROM session_questions sq JOIN questions q
                   ON q.id=sq.question_id WHERE sq.session_id=:session_id
                   ORDER BY sq.position""",
                {"session_id": session_id},
            ).fetchall()
        )

    def question(self, session_id: int, question_id: int) -> Question:
        """Load a question only if it belongs to the specified session."""
        row = self._conn.execute(
            """SELECT q.id, q.question_text, q.question_type, q.select_count
               FROM session_questions sq
               JOIN questions q ON q.id=sq.question_id
               WHERE sq.session_id=:session_id AND q.id=:question_id""",
            {"session_id": session_id, "question_id": question_id},
        ).fetchone()
        if row is None:
            raise ValueError("Question is not part of this session.")
        return self._question(row)

    def _question(self, row: sqlite3.Row) -> Question:
        options = self._conn.execute(
            """SELECT id, answer_text, is_correct, rationale
               FROM answers WHERE question_id=:question_id
               ORDER BY display_order""",
            {"question_id": row["id"]},
        ).fetchall()
        return Question(
            row["id"],
            row["question_text"],
            row["question_type"],
            tuple(
                Option(
                    option["id"],
                    chr(65 + index),
                    option["answer_text"],
                    bool(option["is_correct"]),
                    option["rationale"],
                )
                for index, option in enumerate(options)
            ),
            row["select_count"],
        )

    def has_attempt(self, session_id: int, question_id: int) -> bool:
        """Return whether this session has already recorded this question."""
        return (
            self._conn.execute(
                """SELECT 1 FROM attempts
               WHERE session_id=:session_id AND question_id=:question_id""",
                {"session_id": session_id, "question_id": question_id},
            ).fetchone()
            is not None
        )

    def insert_attempt(
        self,
        session_id: int,
        question_id: int,
        selected: set[int],
        *,
        attempted_at: str,
        is_correct: bool,
        confidence: str | None,
        elapsed_ms: int,
    ) -> None:
        """Insert an attempt and its choices in the caller's transaction."""
        cursor = self._conn.execute(
            """INSERT INTO attempts(
                   session_id, question_id, attempted_at, is_correct,
                   confidence, elapsed_ms)
               VALUES (:session_id, :question_id, :attempted_at, :is_correct,
                       :confidence, :elapsed_ms)""",
            {
                "session_id": session_id,
                "question_id": question_id,
                "attempted_at": attempted_at,
                "is_correct": int(is_correct),
                "confidence": confidence,
                "elapsed_ms": elapsed_ms,
            },
        )
        for option_id in sorted(selected):
            self._conn.execute(
                """INSERT INTO attempt_options(attempt_id, option_id, selected)
                   VALUES (:attempt_id, :option_id, 1)""",
                {"attempt_id": cursor.lastrowid, "option_id": option_id},
            )

    def unanswered_questions(self, session_id: int) -> tuple[int, ...]:
        """Return unanswered question IDs in session order."""
        return tuple(
            row[0]
            for row in self._conn.execute(
                """SELECT sq.question_id FROM session_questions sq
               WHERE sq.session_id=:session_id AND NOT EXISTS (
                   SELECT 1 FROM attempts a WHERE a.session_id=sq.session_id
                   AND a.question_id=sq.question_id)
               ORDER BY sq.position""",
                {"session_id": session_id},
            )
        )

    def complete_session(self, session_id: int, completed_at: str) -> None:
        """Store the completion timestamp without committing independently."""
        self._conn.execute(
            "UPDATE sessions SET completed_at=:completed_at "
            "WHERE id=:session_id",
            {"completed_at": completed_at, "session_id": session_id},
        )
