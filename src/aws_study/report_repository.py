"""Read session and question data used by reports and continuation prompts."""

from __future__ import annotations

from collections import defaultdict

from .report_models import JsonRecord, ReportSessionQuestion
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

    def session_questions(
        self, session_id: int
    ) -> tuple[ReportSessionQuestion, ...]:
        """Batch-load every saved question, including unanswered questions."""
        params = {"session_id": session_id}
        questions = self._conn.execute(
            """SELECT q.*,sq.position,g.group_key selection_group,
                      COALESCE(sr.flagged,0) flagged
               FROM session_questions sq JOIN questions q ON q.id=sq.question_id
               LEFT JOIN session_responses sr ON sr.session_id=sq.session_id
                   AND sr.question_id=sq.question_id
               LEFT JOIN question_selection_groups g ON g.question_id=q.id
               WHERE sq.session_id=:session_id ORDER BY sq.position""",
            params,
        ).fetchall()
        if not questions:
            return ()
        attempts = {
            row["question_id"]: dict(row)
            for row in self._conn.execute(
                "SELECT * FROM attempts WHERE session_id=:session_id",
                params,
            )
        }
        answers: dict[int, list[JsonRecord]] = defaultdict(list)
        selected: dict[int, set[int]] = defaultdict(set)
        for row in self._conn.execute(
            """SELECT sa.question_id,o.id,sa.display_order,o.answer_text,
                      o.is_correct,o.rationale,COALESCE(ao.selected,0) selected
               FROM session_answers sa JOIN answers o ON o.id=sa.answer_id
               LEFT JOIN attempts a ON a.session_id=sa.session_id
                   AND a.question_id=sa.question_id
               LEFT JOIN attempt_options ao ON ao.attempt_id=a.id
                   AND ao.option_id=o.id
               WHERE sa.session_id=:session_id
               ORDER BY sa.question_id,sa.display_order""",
            params,
        ):
            answer = dict(row)
            question_id = answer.pop("question_id")
            if answer.pop("selected"):
                selected[question_id].add(answer["id"])
            answer["label"] = chr(64 + answer["display_order"])
            answers[question_id].append(answer)
        provenance = self.provenance_for_questions(
            {q["id"] for q in questions}
        )
        records = []
        for row in questions:
            question = dict(row)
            question_id = question["id"]
            question["flagged"] = bool(question["flagged"])
            if not answers[question_id]:
                raise ValueError(
                    f"Session {session_id} question {question_id} "
                    "has no saved answer order"
                )
            question["sources"] = provenance.get(question_id, [])
            records.append(
                ReportSessionQuestion(
                    question,
                    tuple(answers[question_id]),
                    attempts.get(question_id),
                    frozenset(selected[question_id]),
                )
            )
        return tuple(records)

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
