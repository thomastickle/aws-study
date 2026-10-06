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
            choices[row["attempt_id"]].append(
                ReportChoice(
                    chr(64 + row["display_order"]),
                    row["answer_text"],
                )
            )
        return {
            attempt_id: tuple(options)
            for attempt_id, options in choices.items()
        }

    def attempts(self, session_id: int) -> tuple[ReportAttempt, ...]:
        """Load attempt choices and all source occurrences for review."""
        params = {"session_id": session_id}
        attempts = self._conn.execute(
            """SELECT a.*, q.question_text, q.area, q.topic
               FROM attempts a JOIN questions q ON q.id=a.question_id
               WHERE a.session_id=:session_id ORDER BY a.id""",
            params,
        ).fetchall()
        selected = self._choices(
            self._conn.execute(
                """SELECT a.id attempt_id, sa.display_order, o.answer_text
               FROM attempts a JOIN attempt_options ao ON ao.attempt_id=a.id
               JOIN answers o ON o.id=ao.option_id
               JOIN session_answers sa ON sa.session_id=a.session_id
               AND sa.question_id=a.question_id AND sa.answer_id=o.id
               WHERE a.session_id=:session_id AND ao.selected=1
               ORDER BY a.id, sa.display_order""",
                params,
            )
        )
        correct = self._choices(
            self._conn.execute(
                """SELECT a.id attempt_id, sa.display_order, o.answer_text
               FROM attempts a JOIN answers o ON o.question_id=a.question_id
               JOIN session_answers sa ON sa.session_id=a.session_id
               AND sa.question_id=a.question_id AND sa.answer_id=o.id
               WHERE a.session_id=:session_id AND o.is_correct=1
               ORDER BY a.id, sa.display_order""",
                params,
            )
        )
        provenance = self.provenance_for_questions(
            {a["question_id"] for a in attempts}
        )
        details = []
        for attempt in attempts:
            record = dict(attempt)
            record["sources"] = provenance.get(attempt["question_id"], [])
            details.append(record)
        return tuple(
            ReportAttempt(
                attempt,
                selected.get(attempt["id"], ()),
                correct.get(attempt["id"], ()),
            )
            for attempt in details
        )

    def question_context(
        self, question_id: int, *, session_id: int | None = None
    ) -> JsonRecord:
        """Load exact question content and provenance for export."""
        row = self._conn.execute(
            """SELECT q.*,g.group_key selection_group FROM questions q
               LEFT JOIN question_selection_groups g ON g.question_id=q.id
               WHERE q.id=:question_id""",
            {"question_id": question_id},
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown question {question_id}")
        question = dict(row)
        if session_id is None:
            answers = self._conn.execute(
                """SELECT id,display_order,answer_text,is_correct,rationale
                   FROM answers WHERE question_id=:question_id ORDER BY display_order""",
                {"question_id": question_id},
            )
        else:
            answers = self._conn.execute(
                """SELECT a.id,sa.display_order,a.answer_text,a.is_correct,a.rationale
                   FROM session_answers sa JOIN answers a ON a.id=sa.answer_id
                   WHERE sa.session_id=:session_id AND sa.question_id=:question_id
                   ORDER BY sa.display_order""",
                {"question_id": question_id, "session_id": session_id},
            )
        question["answers"] = []
        for row in answers:
            answer = dict(row)
            answer["label"] = chr(64 + answer["display_order"])
            question["answers"].append(answer)
        if session_id is not None and not question["answers"]:
            raise ValueError("Question is not part of this session")
        question["sources"] = self.provenance(question_id)
        return question

    def provenance(self, question_id: int) -> list[JsonRecord]:
        """Include every occurrence and its source-specific explanations."""
        return self.provenance_for_questions({question_id}).get(
            question_id, []
        )

    def provenance_for_questions(
        self, question_ids: set[int]
    ) -> dict[int, list[JsonRecord]]:
        """Batch provenance and source answers in two queries for a session."""
        if not question_ids:
            return {}
        params = {
            f"q{index}": qid for index, qid in enumerate(sorted(question_ids))
        }
        binds = ",".join(f":{key}" for key in params)
        sources = [
            dict(r)
            for r in self._conn.execute(
                f"""SELECT qs.*,s.source_key,s.name,s.source_type,s.source_file,
                       s.observed_year,s.verification_origin
                FROM question_sources qs JOIN sources s ON s.id=qs.source_id
                WHERE qs.question_id IN ({binds})
                ORDER BY s.id,qs.source_order,qs.id""",
                params,
            )
        ]
        answers = defaultdict(list)
        for row in self._conn.execute(
            f"""SELECT qsa.question_source_id,a.id,a.answer_text,a.is_correct,
                       qsa.source_order,qsa.rationale
                FROM question_source_answers qsa JOIN answers a ON a.id=qsa.answer_id
                JOIN question_sources qs ON qs.id=qsa.question_source_id
                WHERE qs.question_id IN ({binds})
                ORDER BY qsa.question_source_id,qsa.source_order""",
            params,
        ):
            answer = dict(row)
            occurrence_id = answer.pop("question_source_id")
            answers[occurrence_id].append(answer)
        grouped = defaultdict(list)
        for source in sources:
            source["answers"] = answers[source["id"]]
            grouped[source["question_id"]].append(source)
        return dict(grouped)
