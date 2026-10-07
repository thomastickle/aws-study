"""Scripted terminal navigation, startup selection, and explicit submission."""

import copy
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from unittest.mock import patch

from study_fixture import BankTestCase, question

from aws_study.cli import main
from aws_study.db import connect
from aws_study.quiz import _display_question, choose_saved_exam, run_exam
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


class ExamUITests(BankTestCase):
    def setUp(self):
        super().setUp()
        q = question()
        q["answers"] = [
            {
                "text": f"Answer text {label}",
                "correct": label == "B",
                "rationale": f"Explanation {label}",
            }
            for label in "ABCDEF"
        ]
        self.import_questions([q])
        self.repo = QuizRepository(self.conn)
        self.service = QuizService(self.repo)
        self.sid = self.service.create_session(
            1,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        self.q = self.service.questions(self.sid)[0]

    def run_inputs(self, inputs):
        with (
            patch("builtins.input", side_effect=inputs),
            redirect_stdout(StringIO()) as output,
        ):
            run_exam(self.service, self.sid)
        return output.getvalue()

    def cli(self, args, inputs):
        with (
            patch(
                "aws_study.cli._db",
                side_effect=lambda args: connect(self.root / "study.db"),
            ),
            patch("builtins.input", side_effect=inputs),
            redirect_stdout(StringIO()) as output,
        ):
            main(args)
        return output.getvalue()

    def test_answer_f_is_a_choice_flag_is_a_command_and_reports_wait_for_submission(
        self,
    ):
        text = self.run_inputs([":f", "F", "e", "q"])
        response = self.repo.responses(self.sid)[self.q.id]
        self.assertEqual(response.selected, {self.q.options[5].id})
        self.assertTrue(response.flagged)
        self.assertEqual(response.confidence, "medium")
        self.assertIn("[FLAGGED]", text)
        self.assertNotIn("Score:", text)
        self.assertNotIn("Explanation", text)
        self.assertNotIn("Correct answer(s)", text)
        self.assertIn(f"quiz --resume {self.sid}", text)

    def test_interruption_at_confidence_saves_selection(self):
        for interruption in (KeyboardInterrupt(), EOFError()):
            with self.subTest(interruption=type(interruption).__name__):
                self.run_inputs(["B", interruption])
                self.assertTrue(
                    self.repo.responses(self.sid)[self.q.id].selected
                )
                self.assertEqual(
                    self.conn.execute(
                        "SELECT COUNT(*) FROM attempts"
                    ).fetchone()[0],
                    0,
                )
                self.assertFalse(self.service.is_complete(self.sid))

    def test_review_index_browsing_and_editing_return_to_index(self):
        self.import_questions([question(2), question(3)])
        self.sid = self.service.create_session(
            1,
            count=3,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=2,
        )
        questions = self.service.questions(self.sid)
        for q in questions:
            self.service.save_response(self.sid, q.id, {q.options[0].id})
        self.service.toggle_flag(self.sid, questions[0].id)
        with patch(
            "aws_study.quiz._display_question", wraps=_display_question
        ) as display:
            text = self.run_inputs(["1", ":n", "B", "e", "3", ":p", ":r", "q"])
        self.assertEqual(
            [call.args[1] for call in display.call_args_list], [1, 2, 3, 2]
        )
        self.assertIn("*   1. A [FLAGGED]", text)
        self.assertIn("    2. A", text)
        self.assertIn("    2. B", text)
        self.assertIn(f"> A. {questions[0].options[0].text}", text)
        responses = self.repo.responses(self.sid)
        self.assertEqual(
            responses[questions[1].id].selected, {questions[1].options[1].id}
        )
        self.assertEqual(responses[questions[1].id].confidence, "medium")
        self.assertEqual(responses[questions[0].id].confidence, None)
        self.assertFalse(self.service.is_complete(self.sid))
        self.assertNotIn("Score:", text)
        self.assertNotIn("Explanation", text)

    def test_selected_answers_are_bold_in_full_multi_select_question(self):
        q = replace(self.q, kind="multi_select", select_count=2)
        selected = frozenset(o.id for o in q.options[:2])
        with (
            redirect_stdout(StringIO()) as output,
            patch.object(output, "isatty", return_value=True),
        ):
            _display_question(q, 1, 1, 80, selected=selected)
        text = output.getvalue()
        self.assertEqual(text.count("\033[1m"), 2)
        for o in q.options[:2]:
            self.assertIn(f"\033[1m> {o.label}. {o.text}\033[0m", text)
        self.assertIn(f"  C. {q.options[2].text}", text)

    def test_revisit_change_keep_and_clear_confidence_then_confirm(self):
        self.run_inputs(["A", "c", "q"])
        text = self.run_inputs(
            [
                "r 1",
                "B",
                "",
                "f 1",
                "r 1",
                "",
                "none",
                "s",
                "",
                "r 1",
                "",
                "u",
                "s",
                "yes",
            ]
        )
        self.assertTrue(self.service.is_complete(self.sid))
        response = self.repo.responses(self.sid)[self.q.id]
        self.assertEqual(response.confidence, "low")
        self.assertEqual(response.selected, {self.q.options[1].id})
        self.assertTrue(response.flagged)
        self.assertIn("Score:", text)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1
        )

    def test_question_commands_confidence_commands_and_invalid_inputs(self):
        text = self.run_inputs(
            [
                ":bad",
                "bad",
                "",
                ":p",
                ":flag",
                ":review",
                "s",
                "r 0",
                "r nope",
                "bad",
                "f 1",
                "r 1",
                "A",
                "bad",
                ":f",
                ":r",
                "q",
            ]
        )
        self.assertIn("Unknown command", text)
        self.assertIn("Unknown answer token", text)
        self.assertIn("exactly 1", text)
        self.assertIn("Cannot submit", text)
        self.assertIn("has not been answered", text)
        self.assertIn("[UNANSWERED]", text)
        self.assertIn("Choose C, E, U", text)
        self.assertFalse(self.service.is_complete(self.sid))

    def test_skip_previous_next_and_confidence_navigation_preserve_drafts(
        self,
    ):
        text = self.run_inputs([":next", "r 1", "A", ":p", "", ":next", "q"])
        self.assertTrue(self.repo.responses(self.sid)[self.q.id].selected)
        self.assertIn("[UNANSWERED]", text)
        self.assertFalse(self.service.is_complete(self.sid))
        self.run_inputs(["r 1", "", ":quit"])

    def test_time_accumulates_only_inside_question_visits(self):
        with patch(
            "aws_study.quiz.time.monotonic", side_effect=[0.0, 20.0, 30.0]
        ):
            self.run_inputs(["A", "c", "q"])
        self.assertEqual(
            self.repo.responses(self.sid)[self.q.id].elapsed_ms, 30000
        )
        with patch(
            "aws_study.quiz.time.monotonic",
            side_effect=[1000.0, 1010.0, 1015.0],
        ):
            self.run_inputs(["r 1", "B", "e", "q"])
        self.assertEqual(
            self.repo.responses(self.sid)[self.q.id].elapsed_ms, 45000
        )
        # Review has no active-question clock, including interrupted confirmation.
        with patch(
            "aws_study.quiz.time.monotonic",
            side_effect=AssertionError("Review timed"),
        ):
            self.run_inputs(["s", KeyboardInterrupt()])
        self.assertEqual(
            self.repo.responses(self.sid)[self.q.id].elapsed_ms, 45000
        )

    def test_saved_exam_menu_handles_resume_new_quit_and_bad_input(self):
        for inputs, expected in (
            (["bad", str(self.sid)], self.sid),
            (["n"], None),
            (["q"], 0),
            ([EOFError()], 0),
            ([KeyboardInterrupt()], 0),
        ):
            with (
                patch("builtins.input", side_effect=inputs),
                redirect_stdout(StringIO()) as output,
            ):
                self.assertEqual(
                    choose_saved_exam(self.service, 1, width=80), expected
                )
            self.assertIn("0/1 answered", output.getvalue())
        self.assertIsNone(choose_saved_exam(self.service, 999, width=80))
        text = self.cli(["quiz", "--cert", "TEST-C01"], [str(self.sid), ":q"])
        self.assertIn("Saved draft exams", text)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1
        )
        self.cli(["quiz", "--cert", "TEST-C01"], ["q"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 1
        )
        self.cli(["quiz", "--cert", "TEST-C01"], ["n", ":q"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 2
        )

    def test_resume_cli_does_not_export_before_submission_and_keeps_flags(
        self,
    ):
        target = self.root / "reports"
        self.cli(
            ["quiz", "--resume", str(self.sid), "--reports", str(target)],
            [":f", ":q"],
        )
        self.assertFalse(target.exists())
        correct = next(o.label for o in self.q.options if o.correct)
        self.cli(
            ["quiz", "--resume", str(self.sid), "--reports", str(target)],
            [correct, "c", "s", "y"],
        )
        self.assertEqual(len(list(target.rglob("*-context.json"))), 1)
        self.assertTrue(self.repo.responses(self.sid)[self.q.id].flagged)

    def test_cli_resume_rejects_selection_options_and_draft_learning_exports(
        self,
    ):
        for args in (
            ["quiz", "--resume", str(self.sid), "--count", "2"],
            ["quiz", "--resume", str(self.sid), "--width", "0"],
            ["report", str(self.sid)],
            ["prompt", str(self.sid)],
        ):
            with (
                redirect_stderr(StringIO()),
                self.assertRaises(SystemExit) as result,
            ):
                self.cli(args, [])
            self.assertEqual(result.exception.code, 2)
        with self.assertRaisesRegex(ValueError, "Wrap width"):
            run_exam(self.service, self.sid, width=0)

    def test_multiselect_reprompts_before_confidence_and_deduplicates_tokens(
        self,
    ):
        q = copy.deepcopy(question(2))
        q.update(type="multi_select", select_count=2)
        q["answers"] += [{"text": "Another correct", "correct": True}]
        self.import_questions([q], cert_code="OTHER-C01")
        sid = self.service.create_session(
            2, count=1, target_year=2026, mode="exam", strategy="random"
        )
        item = self.service.questions(sid)[0]
        prompts = []
        values = iter(["A", "A B C", "A; A,b", "e", "q"])

        def answer(prompt):
            prompts.append(prompt)
            if "Confidence" in prompt:
                self.assertEqual(
                    len(self.repo.responses(sid)[item.id].selected), 2
                )
            return next(values)

        with (
            patch("builtins.input", side_effect=answer),
            redirect_stdout(StringIO()) as output,
        ):
            run_exam(self.service, sid)
        self.assertIn("you entered 1", output.getvalue())
        self.assertIn("you entered 3", output.getvalue())
        self.assertEqual(sum("Confidence" in p for p in prompts), 1)
        self.assertEqual(len(self.repo.responses(sid)[item.id].selected), 2)
