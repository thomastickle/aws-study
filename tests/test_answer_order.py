"""Per-session answer permutations stay stable for grading and review."""

import sqlite3
from contextlib import closing

from study_fixture import BankTestCase, question, remove_draft_schema

from aws_study.db import SCHEMA_VERSION, connect, init_db
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.report_repository import ReportRepository
from aws_study.report_service import ReportService


class AnswerOrderTests(BankTestCase):
    def setUp(self):
        super().setUp()
        qs = []
        for index, correct in [(1, "C"), (2, "AF")]:
            q = question(index)
            q.update(
                type="single_select" if len(correct) == 1 else "multi_select",
                select_count=len(correct),
            )
            q["answers"] = [
                {
                    "text": f"Synthetic choice {label}",
                    "correct": label in correct,
                    "rationale": f"Rationale {label}",
                }
                for label in "ABCDEF"
            ]
            qs.append(q)
        self.import_questions(qs)
        self.repository = QuizRepository(self.conn)
        self.service = QuizService(self.repository)

    def session(self, seed=1, mode="exam"):
        return self.service.create_session(
            1,
            count=2,
            target_year=2026,
            mode=mode,
            strategy="random",
            seed=seed,
        )

    def test_orders_are_permutations_vary_by_session_and_keep_content_unchanged(
        self,
    ):
        before = {
            table: [
                tuple(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in (
                "questions",
                "answers",
                "question_sources",
                "question_source_answers",
            )
        }
        canonical = {
            qid: [
                r[0]
                for r in self.conn.execute(
                    "SELECT id FROM answers WHERE question_id=? ORDER BY display_order",
                    (qid,),
                )
            ]
            for qid in (1, 2)
        }
        seen: dict[int, set[tuple[int, ...]]] = {1: set(), 2: set()}
        for seed in range(20):
            for mode in ("exam", "study"):
                sid = self.session(seed, mode)
                qs = self.service.questions(sid)
                for q in qs:
                    ids = tuple(o.id for o in q.options)
                    self.assertEqual(set(ids), set(canonical[q.id]))
                    self.assertEqual(len(ids), 6)
                    self.assertEqual(
                        [o.label for o in q.options], list("ABCDEF")
                    )
                    seen[q.id].add(ids)
        for qid, permutations in seen.items():
            self.assertGreater(len(permutations), 1)
            self.assertTrue(
                any(ids != tuple(canonical[qid]) for ids in permutations)
            )
        for table, rows in before.items():
            self.assertEqual(
                [
                    tuple(r)
                    for r in self.conn.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    )
                ],
                rows,
            )

    def test_seed_repeats_and_reload_never_reshuffles(self):
        first = self.session(17)
        second = self.session(17)
        expected = self.service.questions(first)
        self.assertEqual(expected, self.service.questions(second))
        self.assertEqual(expected, self.service.questions(first))
        with closing(connect(self.root / "study.db")) as restored:
            self.assertEqual(
                QuizService(QuizRepository(restored)).questions(first),
                expected,
            )

    def test_scoring_reports_and_context_use_session_letters_and_order(self):
        sid = self.session(7, mode="study")
        questions = self.service.questions(sid)
        for q in questions:
            selected = {o.id for o in q.options if o.correct}
            result = self.service.record_answer(
                sid, q.id, selected, "low", 150
            )
            self.assertTrue(result.is_correct)
            self.assertEqual(
                result.correct_options,
                tuple(o for o in q.options if o.correct),
            )
        self.service.finish_session(sid)
        service = ReportService(ReportRepository(self.conn))
        bundle = service.session_data(sid)
        by_id = {q.id: q for q in questions}
        for attempt in bundle["attempts"]:
            q = by_id[attempt["question_id"]]
            expected = [f"{o.label}. {o.text}" for o in q.options if o.correct]
            self.assertEqual(attempt["correct"], expected)
            self.assertEqual(attempt["selected"], expected)
        for record in bundle["questions"]:
            q = by_id[record["id"]]
            self.assertEqual(
                [a["id"] for a in record["answers"]], [o.id for o in q.options]
            )
            self.assertEqual(
                [a["label"] for a in record["answers"]],
                [o.label for o in q.options],
            )
            self.assertEqual(
                [a["rationale"] for a in record["answers"]],
                [o.rationale for o in q.options],
            )

    def test_late_answer_order_failure_rolls_back_whole_session(self):
        self.conn.execute(
            """CREATE TRIGGER reject_order BEFORE INSERT ON session_answers
            WHEN NEW.display_order=2 BEGIN SELECT RAISE(ABORT,'Synthetic order failure'); END"""
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "order failure"):
            self.session()
        for table in ("sessions", "session_questions", "session_answers"):
            self.assertEqual(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                    0
                ],
                0,
            )
        self.assertFalse(self.conn.in_transaction)

    def test_answer_cannot_be_moved_to_another_question(self):
        sid = self.session()
        first, second = self.service.questions(sid)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.conn:
                self.conn.execute(
                    "DELETE FROM session_answers WHERE session_id=? AND question_id=? AND display_order=1",
                    (sid, first.id),
                )
                self.conn.execute(
                    "INSERT INTO session_answers VALUES (?,?,?,1)",
                    (sid, first.id, second.options[0].id),
                )
        self.assertEqual(self.service.questions(sid)[0], first)

    def test_schema_three_upgrade_preserves_historical_order_and_attempts(
        self,
    ):
        remove_draft_schema(self.conn)
        self.conn.execute("DROP INDEX idx_attempt_session_question")
        self.conn.execute("DROP INDEX idx_attempt_question_recent")
        self.conn.execute("DROP TRIGGER attempts_nonnegative_elapsed_insert")
        self.conn.execute("DROP TRIGGER attempts_nonnegative_elapsed_update")
        self.conn.execute("DROP TABLE session_answers")
        self.conn.execute("DROP INDEX idx_answer_question_identity")
        self.conn.execute("PRAGMA user_version=3")
        with self.conn:
            sid = self.repository.insert_session(
                1,
                started_at="2026-01-01",
                target_year=2026,
                mode="study",
                strategy="random",
                requested_count=2,
                selected_questions=[(1, 1), (2, 1)],
            )
            selected = {
                r[0]
                for r in self.conn.execute(
                    "SELECT id FROM answers WHERE question_id=1 AND is_correct=1"
                )
            }
            cursor = self.conn.execute(
                """INSERT INTO attempts(session_id,question_id,attempted_at,
                   is_correct,confidence,elapsed_ms)
                   VALUES (:sid,1,'2026-01-01',1,'high',900)""",
                {"sid": sid},
            )
            self.conn.executemany(
                "INSERT INTO attempt_options(attempt_id,option_id) VALUES (:attempt,:option)",
                [
                    {"attempt": cursor.lastrowid, "option": aid}
                    for aid in selected
                ],
            )

        tables = (
            "sessions",
            "session_questions",
            "attempts",
            "attempt_options",
            "answers",
        )
        before = {
            table: [
                tuple(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in tables
        }
        init_db(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        for q in self.service.questions(sid):
            self.assertEqual(
                [o.text for o in q.options],
                [f"Synthetic choice {label}" for label in "ABCDEF"],
            )
        for table, rows in before.items():
            self.assertEqual(
                [
                    tuple(r)
                    for r in self.conn.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    )
                ],
                rows,
            )
        data = ReportService(ReportRepository(self.conn)).session_data(sid)
        self.assertEqual(
            data["attempts"][0]["selected"], ["C. Synthetic choice C"]
        )
        after = self.conn.serialize()
        init_db(self.conn)
        self.assertEqual(self.conn.serialize(), after)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(), []
        )
