from aws_study.selection import candidates
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from study_fixture import BankTestCase, question


class CoreTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.import_questions([question()])

    def test_import_has_no_attempt_history(self):
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0
        )

    def test_adaptive_candidate_weights_miss(self):
        service = QuizService(QuizRepository(self.conn))
        sid = service.create_session(
            1,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        q = service.questions(sid)[0]
        service.record_answer(sid, q.id, {q.options[0].id}, "low", 0)
        self.assertGreater(
            candidates(self.conn, 1, target_year=2026, strategy="adaptive")[
                0
            ].weight,
            5,
        )

    def test_year_filter(self):
        self.assertEqual(
            candidates(self.conn, 1, target_year=2025, strategy="random"), []
        )
