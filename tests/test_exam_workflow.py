"""Persistent drafts are editable exam state, never intermediate learning history."""

import sqlite3
from contextlib import closing

from study_fixture import BankTestCase, question

from aws_study.db import connect
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.report_repository import ReportRepository
from aws_study.report_service import ReportService
from aws_study.selection import rank_candidates
from aws_study.statistics_repository import StatisticsRepository


class ExamWorkflowTests(BankTestCase):
    def setUp(self):
        super().setUp()
        qs = [question(1), question(2), question(3)]
        qs[1].update(type="multi_select", select_count=2)
        qs[1]["answers"] += [
            {
                "text": "Second correct",
                "correct": True,
                "rationale": "Second rationale",
            }
        ]
        self.import_questions(qs)
        self.repo = QuizRepository(self.conn)
        self.service = QuizService(self.repo)
        self.sid = self.service.create_session(
            1,
            count=3,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=2,
        )
        self.questions = self.service.questions(self.sid)

    def answer(self, q, *, correct=True):
        selected = {o.id for o in q.options if o.correct}
        if not correct:
            selected.remove(next(iter(selected)))
            selected.add(next(o.id for o in q.options if not o.correct))
        return selected

    def fill(self):
        for q in self.questions:
            self.service.save_response(self.sid, q.id, self.answer(q), 100)
            self.service.set_confidence(self.sid, q.id, "medium")

    def test_drafts_edit_flags_confidence_and_time_do_not_change_learning(
        self,
    ):
        before = self.repo.candidate_history(1, target_year=2026)
        stats = StatisticsRepository(self.conn).for_certification(1)
        for q in self.questions:
            self.service.save_response(
                self.sid, q.id, self.answer(q, correct=False), 20000
            )
            self.service.set_confidence(self.sid, q.id, "low")
            self.service.toggle_flag(self.sid, q.id)
        self.assertEqual(
            self.repo.candidate_history(1, target_year=2026), before
        )
        self.assertEqual(
            StatisticsRepository(self.conn).for_certification(1), stats
        )
        self.assertEqual(
            rank_candidates(before, strategy="adaptive"),
            rank_candidates(
                self.repo.candidate_history(1, target_year=2026),
                strategy="adaptive",
            ),
        )
        q = self.questions[0]
        self.service.save_response(self.sid, q.id, self.answer(q), 10000)
        self.assertEqual(self.repo.responses(self.sid)[q.id].confidence, "low")
        self.service.set_confidence(self.sid, q.id, None)
        self.service.toggle_flag(self.sid, q.id)
        self.service.add_elapsed(self.sid, q.id, 1)
        response = self.repo.responses(self.sid)[q.id]
        self.assertEqual(response.elapsed_ms, 30001)
        self.assertIsNone(response.confidence)
        self.assertFalse(response.flagged)
        self.assertTrue(response.first_answered_at)
        self.assertTrue(response.updated_at)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM session_responses"
            ).fetchone()[0],
            3,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0
        )
        results = self.service.submit_session(self.sid)
        self.assertEqual(sum(r.is_correct for r in results), 1)
        after = self.repo.candidate_history(1, target_year=2026)
        self.assertEqual(sum(h.attempt_count for h in after), 3)
        self.assertEqual(sum(h.miss_count for h in after), 2)
        rows = self.conn.execute(
            "SELECT * FROM attempts WHERE session_id=? ORDER BY id",
            (self.sid,),
        ).fetchall()
        self.assertEqual(rows[0]["elapsed_ms"], 30001)
        self.assertIsNone(rows[0]["confidence"])
        self.assertTrue(self.service.is_complete(self.sid))
        snapshot = self.conn.serialize()
        self.assertEqual(self.service.submit_session(self.sid), results)
        self.assertEqual(self.conn.serialize(), snapshot)
        context = ReportService(ReportRepository(self.conn)).bundle(self.sid)
        self.assertEqual(
            [q["flagged"] for q in context.questions], [False, True, True]
        )
        self.assertTrue(
            all(isinstance(q["flagged"], bool) for q in context.questions)
        )

    def test_invalid_selections_and_metadata_are_rejected_without_draft_writes(
        self,
    ):
        for q in self.questions:
            for selected in (set(), {999}, {o.id for o in q.options}):
                with self.assertRaises(ValueError):
                    self.service.save_response(self.sid, q.id, selected)
        multi = next(q for q in self.questions if q.select_count == 2)
        with self.assertRaisesRegex(ValueError, "exactly 2"):
            self.service.save_response(
                self.sid, multi.id, {multi.options[0].id}
            )
        q = self.questions[0]
        for operation in (
            lambda: self.service.save_response(
                self.sid, q.id, self.answer(q), -1
            ),
            lambda: self.service.add_elapsed(self.sid, q.id, -1),
            lambda: self.service.set_confidence(self.sid, q.id, "invalid"),
            lambda: self.service.save_response(self.sid, 999, {1}),
            lambda: self.service.toggle_flag(self.sid, 999),
            lambda: self.service.set_confidence(self.sid, 999, "high"),
            lambda: self.service.record_answer(
                self.sid, q.id, self.answer(q), None, 0
            ),
            lambda: self.service.finish_session(self.sid),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertFalse(self.repo.responses(self.sid))

    def test_flag_only_questions_are_unanswered_and_submission_requires_all_answers(
        self,
    ):
        q = self.questions[0]
        self.service.toggle_flag(self.sid, q.id)
        items = self.service.review_items(self.sid)
        self.assertEqual(items[0].status, "UNANSWERED")
        self.assertTrue(items[0].response.flagged)
        self.assertEqual(len(self.service.submission_errors(self.sid)), 3)
        with self.assertRaisesRegex(ValueError, "Cannot submit"):
            self.service.submit_session(self.sid)
        self.fill()
        self.service.submit_session(self.sid)
        for operation in (
            lambda: self.service.save_response(self.sid, q.id, self.answer(q)),
            lambda: self.service.toggle_flag(self.sid, q.id),
            lambda: self.service.set_confidence(self.sid, q.id, "high"),
            lambda: self.service.add_elapsed(self.sid, q.id, 5),
            lambda: self.service.resume_session(self.sid),
        ):
            with self.assertRaisesRegex(ValueError, "already complete"):
                operation()

    def test_finalization_failure_rolls_back_every_attempt_and_preserves_drafts(
        self,
    ):
        self.fill()
        self.conn.execute("""CREATE TRIGGER reject_final BEFORE INSERT ON attempts
            WHEN NEW.question_id=2 BEGIN SELECT RAISE(ABORT,'Finalization failed'); END""")
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "Finalization failed"
        ):
            self.service.submit_session(self.sid)
        self.assertFalse(self.service.is_complete(self.sid))
        for table in ("attempts", "attempt_options"):
            self.assertEqual(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                    0
                ],
                0,
            )
        self.assertEqual(len(self.repo.responses(self.sid)), 3)
        self.conn.execute("DROP TRIGGER reject_final")
        self.service.submit_session(self.sid)

    def test_draft_update_failure_retains_previous_selection(self):
        q = self.questions[0]
        self.service.save_response(self.sid, q.id, self.answer(q))
        snapshot = self.repo.responses(self.sid)
        self.conn.execute("""CREATE TRIGGER reject_draft BEFORE INSERT ON session_response_answers
            BEGIN SELECT RAISE(ABORT,'Draft write failed'); END""")
        with self.assertRaisesRegex(
            sqlite3.IntegrityError, "Draft write failed"
        ):
            self.service.save_response(
                self.sid, q.id, self.answer(q, correct=False)
            )
        self.assertEqual(self.repo.responses(self.sid), snapshot)

    def test_reopen_preserves_order_answers_flags_and_confidence(self):
        q = self.questions[1]
        self.service.save_response(self.sid, q.id, self.answer(q), 123)
        self.service.set_confidence(self.sid, q.id, "high")
        self.service.toggle_flag(self.sid, q.id)
        expected = self.service.review_items(self.sid)
        with closing(connect(self.root / "study.db")) as conn:
            service = QuizService(QuizRepository(conn))
            self.assertEqual(service.resume_session(self.sid), expected)
        self.assertNotEqual(
            [q.id for q in self.questions],
            sorted(q.id for q in self.questions),
        )
        self.assertEqual([i.number for i in expected], [1, 2, 3])
        summary = self.service.saved_exams(1)[0]
        self.assertEqual(
            (summary.answered, summary.total, summary.flagged), (1, 3, 1)
        )

    def test_reports_and_prompt_are_blocked_for_unsubmitted_exams(self):
        reports = ReportService(ReportRepository(self.conn))
        for call in (reports.bundle, reports.session_data):
            with self.assertRaisesRegex(ValueError, "not submitted"):
                call(self.sid)

    def test_incomplete_legacy_selection_is_visible_and_must_be_repaired(self):
        q = next(q for q in self.questions if q.select_count == 2)
        with self.repo.transaction():
            self.repo.save_response(
                self.sid, q.id, {q.options[0].id}, "2026-01-01"
            )
        item = next(
            i
            for i in self.service.review_items(self.sid)
            if i.question.id == q.id
        )
        self.assertEqual(item.status, "INCOMPLETE")
        self.assertTrue(
            any(
                "requires 2" in error
                for error in self.service.submission_errors(self.sid)
            )
        )
        self.service.save_response(self.sid, q.id, self.answer(q))
        self.assertEqual(
            next(
                i
                for i in self.service.review_items(self.sid)
                if i.question.id == q.id
            ).status,
            "ANSWERED",
        )

    def test_mode_membership_and_empty_session_guards(self):
        for mode, kind in (
            ("study", "interactive"),
            ("exam", "imported_baseline"),
        ):
            sid = self.service.create_session(
                1, count=1, target_year=2026, mode=mode, strategy="random"
            )
            self.conn.execute(
                "UPDATE sessions SET source_kind=? WHERE id=?", (kind, sid)
            )
            self.conn.commit()
            with self.assertRaisesRegex(ValueError, "interactive"):
                self.service.resume_session(sid)
            with self.assertRaisesRegex(ValueError, "interactive"):
                self.service.submit_session(sid)
        with self.assertRaisesRegex(ValueError, "Unknown session"):
            self.service.resume_session(999)
        empty = self.service.create_session(
            1, count=1, target_year=2026, mode="exam", strategy="random"
        )
        self.conn.execute(
            "DELETE FROM session_questions WHERE session_id=?", (empty,)
        )
        self.conn.commit()
        for call in (self.service.resume_session, self.service.submit_session):
            with self.assertRaisesRegex(ValueError, "no saved questions"):
                call(empty)
