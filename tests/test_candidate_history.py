"""Candidate history loads newest-first attempts in a constant query count."""

from study_fixture import BankTestCase, question

from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


class CandidateHistoryTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.import_questions([question(index) for index in range(1, 4)])
        service = QuizService(QuizRepository(self.conn))
        for run in range(7):
            sid = service.create_session(
                1,
                count=3,
                target_year=2026,
                mode="study",
                strategy="random",
                seed=run,
            )
            for item in service.questions(sid):
                if item.id == 3:
                    continue
                correct = run % 2 == 0
                selected = {
                    next(
                        option.id
                        for option in item.options
                        if option.correct == correct
                    )
                }
                service.record_answer(
                    sid, item.id, selected, "high" if correct else "low", run
                )

    def test_recent_history_is_batched_capped_and_newest_first(self):
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        history = QuizRepository(self.conn).candidate_history(
            1, target_year=2026
        )
        self.assertEqual(len(statements), 2)
        for item in history:
            expected = list(
                self.conn.execute(
                    "SELECT is_correct, confidence, attempted_at FROM attempts WHERE question_id=? ORDER BY id DESC LIMIT 5",
                    (item.question_id,),
                )
            )
            self.assertEqual(
                [
                    (int(a.is_correct), a.confidence, a.attempted_at)
                    for a in item.recent_attempts
                ],
                [tuple(row) for row in expected],
            )
        self.assertEqual([item.attempt_count for item in history], [7, 7, 0])
        self.assertEqual([item.miss_count for item in history], [3, 3, 0])
        self.assertEqual(
            [len(item.recent_attempts) for item in history], [5, 5, 0]
        )

    def test_recent_history_is_omitted_when_unneeded_or_no_questions_eligible(
        self,
    ):
        for year, expected in ((2026, 3), (2025, 0)):
            for include_recent in (False, True):
                if year == 2026 and include_recent:
                    continue
                statements: list[str] = []
                self.conn.set_trace_callback(statements.append)
                history = QuizRepository(self.conn).candidate_history(
                    1, target_year=year, include_recent=include_recent
                )
                self.conn.set_trace_callback(None)
                self.assertEqual(len(statements), 1)
                self.assertEqual(len(history), expected)
                self.assertTrue(
                    all(not item.recent_attempts for item in history)
                )
