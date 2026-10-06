"""Read session and question data used by reports and continuation prompts."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable

from .report_models import JsonRecord, ReportAttempt, ReportChoice
from .repository import SQLiteRepository


class ReportRepository(SQLiteRepository):
    """Load report records without formatting text or writing files."""

    def latest_interactive_session(self) -> int:
        """Resolve the most recent quiz, excluding imported baselines."""
        row = self._conn.execute(
            """SELECT id FROM sessions WHERE source_kind='interactive'
               ORDER BY id DESC LIMIT 1""",
        ).fetchone()
        if row is None:
            raise ValueError("No interactive sessions found.")
        return int(row["id"])

    def session(self, session_id: int) -> JsonRecord:
        """Return stored session metadata joined to its certification."""
        row = self._conn.execute(
            """SELECT s.*, c.provider, c.code, c.name cert_name
               FROM sessions s JOIN certifications c ON c.id=s.certification_id
               WHERE s.id=:session_id""",
            {"session_id": session_id},
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown session {session_id}")
        return dict(row)

    @staticmethod
    def _choices(
        rows: Iterable[sqlite3.Row],
    ) -> dict[int, tuple[ReportChoice, ...]]:
        choices: dict[int, list[ReportChoice]] = defaultdict(list)
        for row in rows:
            choices[row["attempt_id"]].append(ReportChoice(
                row["option_label"], row["option_text"],
            ))
        return {attempt_id: tuple(options)
                for attempt_id, options in choices.items()}

    def attempts(self, session_id: int) -> tuple[ReportAttempt, ...]:
        """Load attempt history and choices with three queries per session."""
        params = {"session_id": session_id}
        attempts = self._conn.execute(
            """SELECT a.*, q.question_text, q.topic, q.concept, q.domain,
                      q.external_key, src.name source_name, src.source_key,
                      q.source_question_number
               FROM attempts a JOIN questions q ON q.id=a.question_id
               JOIN sources src ON src.id=q.source_id
               WHERE a.session_id=:session_id ORDER BY a.id""",
            params,
        ).fetchall()
        selected = self._choices(self._conn.execute(
            """SELECT a.id attempt_id, o.option_label, o.option_text
               FROM attempts a JOIN attempt_options ao ON ao.attempt_id=a.id
               JOIN options o ON o.id=ao.option_id
               WHERE a.session_id=:session_id AND ao.selected=1
               ORDER BY a.id, o.option_order""",
            params,
        ))
        correct = self._choices(self._conn.execute(
            """SELECT a.id attempt_id, o.option_label, o.option_text
               FROM attempts a JOIN options o ON o.question_id=a.question_id
               WHERE a.session_id=:session_id AND o.is_correct=1
               ORDER BY a.id, o.option_order""",
            params,
        ))
        return tuple(ReportAttempt(
            dict(attempt), selected.get(attempt["id"], ()),
            correct.get(attempt["id"], ()),
        ) for attempt in attempts)

    def question_context(self, question_id: int) -> JsonRecord:
        """Load exact question content and provenance for export."""
        row = self._conn.execute(
            """SELECT q.*, s.name source_name, s.source_type, s.observed_year,
                      s.verification_status source_verification
               FROM questions q JOIN sources s ON s.id=q.source_id
               WHERE q.id=:question_id""",
            {"question_id": question_id},
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown question {question_id}")
        question = dict(row)
        question["options"] = [dict(option) for option in self._conn.execute(
            """SELECT option_order, option_label, option_text, is_correct,
                      rationale FROM options WHERE question_id=:question_id
               ORDER BY option_order""",
            {"question_id": question_id},
        )]
        return question
