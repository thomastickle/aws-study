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
                WHERE {" AND ".join(where)} GROUP BY q.id ORDER BY q.id""",
            params,
        ).fetchall()
        recent = (
            self._recent_attempts(
                [row["id"] for row in rows if row["attempts"]]
            )
            if include_recent
            else {}
        )
        return tuple(
            QuestionHistory(
                question_id=row["id"],
                attempt_count=row["attempts"],
                miss_count=row["misses"] or 0,
                recent_attempts=recent.get(row["id"], ()),
                selection_group=row["group_key"],
                normalized_stem=normalize_match_text(row["question_text"]),
            )
            for row in rows
        )

    def _recent_attempts(
        self, question_ids: Sequence[int]
    ) -> dict[int, tuple[AttemptHistory, ...]]:
        """Batch newest-five histories for eligible questions in one query."""
        if not question_ids:
            return {}
        params = {
            f"question_{index}": qid for index, qid in enumerate(question_ids)
        }
        binds = ",".join(f":{name}" for name in params)
        grouped: dict[int, list[AttemptHistory]] = {}
        for attempt in self._conn.execute(
            f"""WITH recent AS (
                    SELECT question_id, is_correct, confidence, attempted_at,
                           ROW_NUMBER() OVER (PARTITION BY question_id ORDER BY id DESC) AS position
                    FROM attempts WHERE question_id IN ({binds})
                ) SELECT question_id, is_correct, confidence, attempted_at FROM recent
                  WHERE position <= 5 ORDER BY question_id, position""",
            params,
        ):
            grouped.setdefault(attempt["question_id"], []).append(
                AttemptHistory(
                    bool(attempt["is_correct"]),
                    attempt["confidence"],
                    attempt["attempted_at"],
                )
            )
        return {qid: tuple(attempts) for qid, attempts in grouped.items()}

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
        session_id = self.inserted_id(cursor)
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

    def answer_ids(self, question_ids: Sequence[int]) -> dict[int, list[int]]:
        """Load all canonical answer IDs in a stable order for shuffling."""
        if not question_ids:
            return {}
        params = {f"q{index}": qid for index, qid in enumerate(question_ids)}
        binds = ",".join(f":{key}" for key in params)
        grouped: dict[int, list[int]] = {}
        for row in self._conn.execute(
            f"""SELECT id,question_id FROM answers WHERE question_id IN ({binds})
                ORDER BY question_id,display_order""",
            params,
        ):
            grouped.setdefault(row["question_id"], []).append(row["id"])
        return grouped

    def save_answer_order(
        self,
        session_id: int,
        question_id: int,
        answer_ids: Sequence[int],
    ) -> None:
        """Save a session's complete answer permutation in its transaction."""
        self._conn.executemany(
            """INSERT INTO session_answers(session_id,question_id,answer_id,display_order)
               VALUES (:session_id,:question_id,:answer_id,:display_order)""",
            (
                {
                    "session_id": session_id,
                    "question_id": question_id,
                    "answer_id": answer_id,
                    "display_order": order,
                }
                for order, answer_id in enumerate(answer_ids, 1)
            ),
        )

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
            self._question(row, session_id)
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
        return self._question(row, session_id)

    def _question(self, row: sqlite3.Row, session_id: int) -> Question:
        options = self._conn.execute(
            """SELECT a.id,a.answer_text,a.is_correct,a.rationale,sa.display_order
               FROM session_answers sa JOIN answers a ON a.id=sa.answer_id
               WHERE sa.session_id=:session_id AND sa.question_id=:question_id
               ORDER BY sa.display_order""",
            {"session_id": session_id, "question_id": row["id"]},
        ).fetchall()
        return Question(
            row["id"],
            row["question_text"],
            row["question_type"],
            tuple(
                Option(
                    option["id"],
                    chr(64 + option["display_order"]),
                    option["answer_text"],
                    bool(option["is_correct"]),
                    option["rationale"],
                )
                for option in options
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
        attempt_id = self.inserted_id(cursor)
        for option_id in sorted(selected):
            self._conn.execute(
                """INSERT INTO attempt_options(attempt_id, option_id, selected)
                   VALUES (:attempt_id, :option_id, 1)""",
                {"attempt_id": attempt_id, "option_id": option_id},
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
