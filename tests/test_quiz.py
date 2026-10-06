import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from aws_study.db import connect, init_db
from aws_study.importers import import_internal_bank
from aws_study.quiz import _parse_answer, _prompt_confidence, run_quiz
from aws_study.quiz_models import AttemptHistory, QuestionHistory
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.selection import rank_candidates


class QuizTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.conn = connect(self.path / "study.db")
        init_db(self.conn)
        bank = {
            "schema_version": 1,
            "questions": [
                {
                    "source": "synthetic", "source_index": index,
                    "question": f"Synthetic question {index}?",
                    "question_type": kind, "dedup_role": "canonical",
                    "options": [
                        {
                            "letter": label, "label": f"Choice {label}",
                            "correct": label in correct, "selected": False,
                            "rationale": f"Explanation {label}",
                        }
                        for label in "ABCDEF"
                    ],
                }
                for index, kind, correct in [
                    (1, "single_select", "B"), (2, "multi_select", "AF"),
                ]
            ],
        }
        bank_path = self.path / "bank.json"
        bank_path.write_text(json.dumps(bank), encoding="utf-8")
        import_internal_bank(
            self.conn, bank_path, cert_code="CLF-C02", observed_year=2026,
            verified_year=2026, import_baseline_attempts=False,
        )
        self.cert_id = self.conn.execute(
            "SELECT id FROM certifications",
        ).fetchone()[0]
        self.repository = QuizRepository(self.conn)
        self.service = QuizService(self.repository)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def session(self, **overrides):
        settings = {
            "count": 2, "target_year": 2026, "mode": "exam",
            "strategy": "random", "seed": 1,
        }
        settings.update(overrides)
        return self.service.create_session(self.cert_id, **settings)

    @staticmethod
    def correct_ids(question):
        return {option.id for option in question.options if option.correct}

    def test_session_order_pool_cap_and_durable_question_models(self):
        first = self.session(count=20)
        second = self.session(count=20)
        questions = self.service.questions(first)
        self.assertEqual(
            [q.id for q in questions],
            [q.id for q in self.service.questions(second)],
        )
        self.assertEqual(len(questions), 2)
        self.assertEqual(
            [row[0] for row in self.conn.execute(
                "SELECT position FROM session_questions "
                "WHERE session_id=? ORDER BY position", (first,),
            )], [1, 2],
        )
        restored = connect(self.path / "study.db")
        try:
            service = QuizService(QuizRepository(restored))
            self.assertEqual(service.questions(first), questions)
        finally:
            restored.close()

    def test_session_creation_rolls_back_if_question_write_fails(self):
        self.conn.execute("""
            CREATE TRIGGER reject_question BEFORE INSERT ON session_questions
            WHEN NEW.position=2
            BEGIN SELECT RAISE(ABORT, 'Synthetic session failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "session failure"):
            self.session()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sessions",
            ).fetchone()[0], 0,
        )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM session_questions",
        ).fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER reject_question")
        self.assertEqual(len(self.service.questions(self.session())), 2)

    def test_answer_scoring_stored_choices_and_null_confidence(self):
        session_id = self.session()
        questions = self.service.questions(session_id)
        for question in questions:
            selected = set(reversed(sorted(self.correct_ids(question))))
            result = self.service.record_answer(
                session_id, question.id, selected, None, 125,
            )
            self.assertTrue(result.is_correct)
            self.assertEqual(result.selected_options, result.correct_options)
            attempt = self.conn.execute(
                "SELECT * FROM attempts WHERE session_id=? AND question_id=?",
                (session_id, question.id),
            ).fetchone()
            self.assertIsNone(attempt["confidence"])
            self.assertEqual(attempt["elapsed_ms"], 125)
            stored_ids = {row[0] for row in self.conn.execute(
                "SELECT option_id FROM attempt_options WHERE attempt_id=?",
                (attempt["id"],),
            )}
            self.assertEqual(stored_ids, selected)
        self.service.finish_session(session_id)
        self.assertTrue(self.repository.is_complete(session_id))
        # Finishing twice must not perform another completion write.
        self.conn.execute("""
            CREATE TRIGGER reject_completion BEFORE UPDATE OF completed_at
            ON sessions
            BEGIN SELECT RAISE(ABORT, 'Repeated completion'); END
        """)
        self.service.finish_session(session_id)

    def test_multi_select_exact_set_and_extra_single_choices(self):
        for selected_labels in ("A", "AFB", "FA"):
            session_id = self.session()
            question = next(
                q for q in self.service.questions(session_id)
                if q.kind == "multi_select"
            )
            selected = {
                o.id for o in question.options if o.label in selected_labels
            }
            result = self.service.record_answer(
                session_id, question.id, selected, "medium", 1,
            )
            self.assertEqual(result.is_correct, selected_labels == "FA")
        session_id = self.session()
        single = self.service.questions(session_id)[0]
        # Preserve incorrect scoring for excess single-select choices.
        result = self.service.record_answer(
            session_id, single.id, {o.id for o in single.options[:2]}, None, 1,
        )
        self.assertFalse(result.is_correct)

    def test_failed_option_write_rolls_back_attempt_and_all_choices(self):
        session_id = self.session()
        question = self.service.questions(session_id)[1]
        selected = self.correct_ids(question)
        rejected_id = max(selected)
        self.conn.execute(f"""
            CREATE TRIGGER reject_option BEFORE INSERT ON attempt_options
            WHEN NEW.option_id={rejected_id}
            BEGIN SELECT RAISE(ABORT, 'Synthetic option failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "option failure"):
            self.service.record_answer(
                session_id, question.id, selected, "low", 200,
            )
        for table in ("attempts", "attempt_options"):
            self.assertEqual(self.conn.execute(
                f"SELECT COUNT(*) FROM {table}",
            ).fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)
        self.conn.execute("DROP TRIGGER reject_option")
        self.assertTrue(self.service.record_answer(
            session_id, question.id, selected, "low", 200,
        ).is_correct)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM attempt_options",
        ).fetchone()[0], 2)

    def test_validation_prevents_invalid_and_duplicate_attempts(self):
        session_id = self.session()
        question, other = self.service.questions(session_id)
        for selected, confidence, elapsed in [
            (set(), None, 0), ({other.options[0].id}, None, 0),
            (self.correct_ids(question), "unknown", 0),
            (self.correct_ids(question), None, -1),
        ]:
            with self.assertRaises(ValueError):
                self.service.record_answer(
                    session_id, question.id, selected, confidence, elapsed,
                )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM attempts",
        ).fetchone()[0], 0)
        self.service.record_answer(
            session_id, question.id, self.correct_ids(question), "high", 1,
        )
        with self.assertRaisesRegex(ValueError, "already been answered"):
            self.service.record_answer(
                session_id, question.id, self.correct_ids(question), None, 1,
            )
        with self.assertRaisesRegex(ValueError, "every question"):
            self.service.finish_session(session_id)
        self.assertFalse(self.repository.is_complete(session_id))
        self.service.record_answer(
            session_id, other.id, self.correct_ids(other), None, 1,
        )
        self.service.finish_session(session_id)
        with self.assertRaisesRegex(ValueError, "already complete"):
            self.service.record_answer(
                session_id, question.id, self.correct_ids(question), None, 1,
            )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM attempts",
        ).fetchone()[0], 2)

    def test_question_membership_and_missing_sessions(self):
        session_id = self.session(count=1)
        outside = self.conn.execute(
            "SELECT id FROM questions WHERE id NOT IN "
            "(SELECT question_id FROM session_questions WHERE session_id=?)",
            (session_id,),
        ).fetchone()[0]
        with self.assertRaisesRegex(ValueError, "not part"):
            self.service.record_answer(session_id, outside, {1}, None, 1)
        for operation in (
            lambda: self.service.questions(999),
            lambda: self.service.record_answer(999, outside, {1}, None, 1),
            lambda: self.service.finish_session(999),
        ):
            with self.assertRaisesRegex(ValueError, "Unknown session"):
                operation()

    def test_bad_settings_and_freshness_do_not_create_sessions(self):
        for settings in (
            {"count": 0}, {"count": -1}, {"mode": "invalid"},
            {"strategy": "invalid"}, {"target_year": 2027},
        ):
            with self.assertRaises(ValueError):
                self.session(**settings)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sessions",
        ).fetchone()[0], 0)
        session_id = self.session(target_year=2027, include_unverified=True)
        self.assertEqual(len(self.service.questions(session_id)), 2)

    def test_labels_are_uppercase_and_missing_labels_use_option_order(self):
        self.conn.execute("UPDATE options SET option_label=NULL")
        self.conn.commit()
        question = self.service.questions(self.session())[0]
        self.assertEqual([o.label for o in question.options], list("ABCDEF"))
        # Restore lowercase stored labels to exercise normalization as well.
        for option in question.options:
            self.conn.execute(
                "UPDATE options SET option_label=? WHERE id=?",
                (option.label.lower(), option.id),
            )
        self.conn.commit()
        question = self.service.questions(self.session())[0]
        self.assertEqual([o.label for o in question.options], list("ABCDEF"))

    def test_cli_study_and_exam_feedback_timing_and_score(self):
        for mode in ("study", "exam"):
            output = StringIO()
            answers = iter(("a", "Confident", "f ;,, A a", ""))
            calls = 0

            def answer(prompt):
                nonlocal calls
                if calls == 2:
                    text = output.getvalue()
                    self.assertEqual("Correct answer(s):" in text,
                                     mode == "study")
                    self.assertEqual("Explanation B" in text,
                                     mode == "study")
                calls += 1
                return next(answers)

            with patch("builtins.input", side_effect=answer):
                with redirect_stdout(output):
                    session_id = run_quiz(
                        self.service, self.cert_id, count=2, target_year=2026,
                        mode=mode, strategy="random", seed=1,
                    )
            self.assertIn("Score: 1/2 (50.0%)", output.getvalue())
            if mode == "exam":
                self.assertIn("Q1: ✗ selected A. Choice A", output.getvalue())
            self.assertTrue(self.repository.is_complete(session_id))
            confidences = [row[0] for row in self.conn.execute(
                "SELECT confidence FROM attempts WHERE session_id=? "
                "ORDER BY id", (session_id,),
            )]
            self.assertEqual(confidences, ["high", None])

    def test_cli_interruption_keeps_previous_answer_without_completion(self):
        with (
            patch("builtins.input", side_effect=["B", "e", KeyboardInterrupt]),
            redirect_stdout(StringIO()),
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_quiz(
                    self.service, self.cert_id, count=2, target_year=2026,
                    mode="exam", strategy="random", seed=1,
                )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM attempts",
        ).fetchone()[0], 1)
        self.assertIsNone(self.conn.execute(
            "SELECT completed_at FROM sessions",
        ).fetchone()[0])
        self.assertFalse(self.conn.in_transaction)

    def test_answer_and_confidence_shortcuts_are_preserved(self):
        self.assertEqual(_parse_answer("F ;, a a", list("ABCDEF")), {0, 5})
        self.assertEqual(_parse_answer("1, 6", list("ABCDEF")), {0, 5})
        for raw, expected in (
            ("C", "high"), ("Educated Guess", "medium"),
            ("u", "low"), ("Unsure", "low"), ("", None),
        ):
            with patch("builtins.input", return_value=raw):
                self.assertEqual(_prompt_confidence(), expected)

    def test_long_quiz_text_and_prompts_fit_narrow_terminals(self):
        text = (
            "This synthetic description contains enough separate words to "
            "wrap across a narrow terminal while preserving readable text."
        )
        self.conn.execute("UPDATE questions SET question_text=?", (text,))
        self.conn.execute(
            "UPDATE options SET option_text=?, rationale=?", (text, text),
        )
        self.conn.commit()
        for mode in ("exam", "study"):
            with self.subTest(mode=mode):
                answers = iter(("a", "C", "a", "U"))
                prompts = []

                def answer(prompt):
                    prompts.append(prompt)
                    return next(answers)

                with (
                    patch("aws_study.terminal.shutil.get_terminal_size",
                          return_value=os.terminal_size((52, 24))),
                    patch("builtins.input", side_effect=answer),
                    redirect_stdout(StringIO()) as output,
                ):
                    session_id = run_quiz(
                        self.service, self.cert_id, count=2, target_year=2026,
                        mode=mode, strategy="random", seed=1,
                    )
                lines = output.getvalue().splitlines()
                self.assertTrue(all(len(line) <= 50 for line in lines))
                self.assertTrue(all(len(line) <= 50 for prompt in prompts
                                    for line in prompt.splitlines()))
                self.assertIn("     across a narrow terminal", output.getvalue())
                self.assertEqual("rationale:" in output.getvalue(),
                                 mode == "study")
                self.assertEqual("Q1:" in output.getvalue(), mode == "exam")
                self.assertTrue(self.repository.is_complete(session_id))

    def test_invalid_wrap_width_does_not_create_a_session(self):
        with self.assertRaisesRegex(ValueError, "Wrap width must be"):
            run_quiz(
                self.service, self.cert_id, count=2, target_year=2026,
                mode="exam", strategy="random", seed=1, width=0,
            )
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sessions",
        ).fetchone()[0], 0)


class SelectionTests(unittest.TestCase):
    def test_weights_and_strategy_filters_without_database(self):
        recent = "2026-10-06T00:00:00+00:00"
        old = "2026-09-01T00:00:00+00:00"
        history = (
            QuestionHistory(1, 0, 0, ()),
            QuestionHistory(2, 2, 1, (AttemptHistory(False, "low", recent),)),
            QuestionHistory(3, 3, 0, (AttemptHistory(True, None, old),) * 3),
        )
        with patch("aws_study.selection.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 6, tzinfo=timezone.utc)
            clock.fromisoformat.side_effect = datetime.fromisoformat
            weights = {
                candidate.question_id: candidate.weight
                for candidate in rank_candidates(history, strategy="adaptive")
            }
            self.assertEqual(weights[1], 4)
            self.assertEqual(weights[2], 11)
            self.assertAlmostEqual(weights[3], 1.8)
            self.assertEqual(
                [c.question_id for c in rank_candidates(history,
                                                       strategy="new")], [1],
            )
            self.assertEqual(
                [c.question_id for c in rank_candidates(history,
                                                       strategy="weak")], [2],
            )
            self.assertTrue(all(
                c.weight == 1 for c in rank_candidates(history,
                                                      strategy="random")
            ))


if __name__ == "__main__":
    unittest.main()
