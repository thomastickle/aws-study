import copy
import json
import tempfile
import unittest
from contextlib import chdir, redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from study_fixture import (
    bank as v2_bank,
)
from study_fixture import (
    converted_fixture,
)
from study_fixture import (
    question as fixture_question,
)

from aws_study.cli import main
from aws_study.db import connect, init_db
from aws_study.importers import import_internal_bank
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.report_repository import ReportRepository
from aws_study.report_service import ReportService
from aws_study.reporting import continuation_prompt, write_report_bundle


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "study.db")
        init_db(self.conn)
        self.reports = ReportService(ReportRepository(self.conn))
        bank = {
            "schema_version": 1,
            "questions": [
                {
                    "source": "synthetic",
                    "source_index": 1,
                    "question": "Synthetic report test?",
                    "question_type": "single_select",
                    "dedup_role": "canonical",
                    "options": [
                        {
                            "letter": "A",
                            "label": "Wrong",
                            "correct": False,
                            "selected": False,
                            "rationale": "Wrong explanation",
                        },
                        {
                            "letter": "B",
                            "label": "Right",
                            "correct": True,
                            "selected": False,
                            "rationale": "Right explanation",
                        },
                    ],
                }
            ],
        }
        bank_path = self.root / "bank.json"
        bank_path.write_text(
            json.dumps(converted_fixture(bank)), encoding="utf-8"
        )
        import_internal_bank(self.conn, bank_path)
        self.cert_id = self.conn.execute(
            "SELECT id FROM certifications",
        ).fetchone()[0]
        service = QuizService(QuizRepository(self.conn))
        self.session_id = service.create_session(
            self.cert_id,
            count=1,
            target_year=2026,
            mode="study",
            strategy="random",
            seed=1,
        )
        q = service.questions(self.session_id)[0]
        wrong = next(o for o in q.options if not o.correct)
        correct = next(o for o in q.options if o.correct)
        self.selected_text = f"{wrong.label}. {wrong.text}"
        self.correct_text = f"{correct.label}. {correct.text}"
        service.record_answer(self.session_id, q.id, {wrong.id}, "low", 0)
        service.finish_session(self.session_id)
        self.clock = patch("aws_study.reporting.datetime")
        self.mock_datetime = self.clock.start()
        self.mock_datetime.now.return_value = datetime(2026, 10, 6, 14, 30, 12)
        self.addCleanup(self.clock.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_named_folder_contains_complete_bundle(self):
        parent = self.root / "exports"
        paths = write_report_bundle(
            self.reports.session_data(self.session_id), parent
        )
        folder = parent / "CLF-C02" / "20261006-143012"
        self.assertEqual({path.parent for path in paths.values()}, {folder})
        self.assertEqual(
            {path.name for path in folder.iterdir()},
            {
                f"session-{self.session_id}-{suffix}"
                for suffix in ("report.md", "prompt.txt", "context.json")
            },
        )
        report = paths["report"].read_text(encoding="utf-8")
        self.assertIn("Score: 0/1 (0.0%)", report)
        self.assertIn(f"Selected: {self.selected_text}", report)
        self.assertIn(f"Correct: {self.correct_text}", report)
        prompt = paths["prompt"].read_text(encoding="utf-8")
        self.assertIn("AWS CLF-C02", prompt)
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertEqual(context["session"]["id"], self.session_id)
        self.assertEqual(context["continuation_prompt"], prompt.rstrip("\n"))
        self.assertEqual(
            context["questions"][0]["question_text"],
            "Synthetic report test?",
        )

    def test_same_second_exports_preserve_previous_files(self):
        parent = self.root / "exports"
        first = write_report_bundle(
            self.reports.session_data(self.session_id), parent
        )
        first["report"].write_text("Previous report", encoding="utf-8")
        second = write_report_bundle(
            self.reports.session_data(self.session_id), parent
        )
        self.assertNotEqual(first["report"].parent, second["report"].parent)
        self.assertEqual(second["report"].parent.name, "20261006-143012-2")
        self.assertEqual(
            first["report"].read_text(encoding="utf-8"),
            "Previous report",
        )
        self.assertIn(
            "Score: 0/1",
            second["report"].read_text(encoding="utf-8"),
        )

    def test_exam_code_groups_exports_and_year_stays_in_metadata(self):
        self.conn.execute(
            "UPDATE certifications SET code='SAA-C03' WHERE id=?",
            (self.cert_id,),
        )
        self.conn.execute(
            "UPDATE sessions SET target_year=2027 WHERE id=?",
            (self.session_id,),
        )
        self.conn.commit()
        paths = write_report_bundle(
            self.reports.session_data(self.session_id),
            self.root / "exports",
        )
        self.assertEqual(
            paths["report"].parent,
            self.root / "exports" / "SAA-C03" / "20261006-143012",
        )
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertEqual(context["session"]["target_year"], 2027)
        self.assertIn(
            "Target year: 2027",
            paths["report"].read_text(encoding="utf-8"),
        )
        self.conn.execute(
            "UPDATE sessions SET target_year=NULL WHERE id=?",
            (self.session_id,),
        )
        self.conn.commit()
        paths = write_report_bundle(
            self.reports.session_data(self.session_id),
            self.root / "exports",
        )
        self.assertEqual(paths["report"].parent.parent.name, "SAA-C03")
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertIsNone(context["session"]["target_year"])

    def test_exam_code_cannot_create_nested_paths(self):
        self.conn.execute(
            "UPDATE certifications SET code='../odd cert/CLF-C02' WHERE id=?",
            (self.cert_id,),
        )
        self.conn.commit()
        parent = self.root / "exports"
        paths = write_report_bundle(
            self.reports.session_data(self.session_id), parent
        )
        self.assertEqual(paths["report"].parent.parent.parent, parent)
        self.assertEqual(
            paths["report"].parent.parent.name, "odd-cert-CLF-C02"
        )

    def test_quiz_and_report_commands_use_new_default_parent(self):
        with (
            chdir(self.root),
            patch(
                "aws_study.cli._db",
                side_effect=lambda args: connect(self.root / "study.db"),
            ),
        ):
            with redirect_stdout(StringIO()) as output:
                main(["report", str(self.session_id)])
            expected = (
                Path(
                    "private/reports/CLF-C02/20261006-143012",
                )
                / f"session-{self.session_id}-report.md"
            )
            self.assertTrue(expected.is_file())
            self.assertIn(str(expected), output.getvalue())
            with (
                patch("builtins.input", side_effect=["B", "C", "s", "y"]),
                redirect_stdout(StringIO()),
            ):
                main(
                    [
                        "quiz",
                        "--cert",
                        "CLF-C02",
                        "--year",
                        "2026",
                        "-n",
                        "1",
                    ]
                )
            folders = list(Path("private/reports/CLF-C02").iterdir())
            self.assertEqual(len(folders), 2)
            self.assertTrue(
                all(
                    len(list(folder.glob("session-*-report.md"))) == 1
                    for folder in folders
                )
            )
            self.assertFalse(Path("private/report").exists())

    def test_prepared_bundle_renders_without_database_connection(self):
        bundle = self.reports.session_data(self.session_id)
        self.conn.close()
        paths = write_report_bundle(bundle, self.root / "offline")
        report = paths["report"].read_text(encoding="utf-8")
        self.assertIn(f"Selected: {self.selected_text}", report)
        self.assertIn(f"Correct: {self.correct_text}", report)
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertEqual(len(context["questions"]), 1)

    def test_latest_ignores_later_imported_baselines_and_unknown_sessions(
        self,
    ):
        self.conn.execute(
            "INSERT INTO sessions(certification_id, source_kind) VALUES (?, 'imported_baseline')",
            (self.cert_id,),
        )
        self.conn.commit()
        self.assertEqual(
            self.reports.resolve_session("latest"), self.session_id
        )
        self.assertEqual(
            self.reports.resolve_session(str(self.session_id)), self.session_id
        )
        with self.assertRaisesRegex(ValueError, "Unknown session"):
            self.reports.session_data(9999)
        self.conn.execute(
            "DELETE FROM sessions WHERE source_kind='interactive'"
        )
        self.conn.commit()
        with self.assertRaisesRegex(
            ValueError, "No reportable interactive sessions"
        ):
            self.reports.resolve_session("latest")

    def test_latest_skips_newer_draft_exams_for_report_and_prompt(self):
        service = QuizService(QuizRepository(self.conn))
        completed = service.create_session(
            self.cert_id,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        q = service.questions(completed)[0]
        service.save_response(
            completed, q.id, {o.id for o in q.options if o.correct}
        )
        service.submit_session(completed)
        draft = service.create_session(
            self.cert_id,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        self.assertEqual(self.reports.resolve_session("latest"), completed)
        with patch(
            "aws_study.cli._db",
            side_effect=lambda args: connect(self.root / "study.db"),
        ):
            with redirect_stdout(StringIO()) as output:
                main(
                    [
                        "report",
                        "latest",
                        "--reports",
                        str(self.root / "latest"),
                    ]
                )
            context_path = next((self.root / "latest").rglob("*-context.json"))
            self.assertEqual(
                json.loads(context_path.read_text())["session"]["id"],
                completed,
            )
            with redirect_stdout(StringIO()) as output:
                main(["prompt", "latest"])
            self.assertEqual(
                output.getvalue(),
                continuation_prompt(self.reports.session_data(completed))
                + "\n",
            )
            for command in ("report", "prompt"):
                with (
                    redirect_stderr(StringIO()) as error,
                    self.assertRaises(SystemExit) as status,
                ):
                    main([command, str(draft)])
                self.assertEqual(status.exception.code, 2)
                self.assertIn("not submitted", error.getvalue())
            self.conn.execute("DELETE FROM sessions WHERE id<>?", (draft,))
            self.conn.commit()
            for command in ("report", "prompt"):
                with (
                    redirect_stderr(StringIO()) as error,
                    self.assertRaises(SystemExit) as status,
                ):
                    main([command, "latest"])
                self.assertEqual(status.exception.code, 2)
                self.assertIn(
                    "No reportable interactive sessions", error.getvalue()
                )

    def test_latest_keeps_empty_and_partial_study_sessions_reportable(self):
        service, sid, questions = self._session(2)
        self.assertEqual(self.reports.resolve_session("latest"), sid)
        self.assertFalse(self.reports.session_data(sid)["attempts"])
        q = questions[0]
        service.record_answer(
            sid, q.id, {o.id for o in q.options if o.correct}, "low", 0
        )
        self.assertEqual(self.reports.resolve_session("latest"), sid)
        self.assertEqual(len(self.reports.session_data(sid)["attempts"]), 1)

    def _session(self, count, *, multi_select=False, seed=7):
        questions = []
        for index in range(1, count + 1):
            question = fixture_question(index)
            question["classification"]["topic"] = f"Report topic {index}"
            for answer in question["answers"]:
                kind = "correct" if answer["correct"] else "wrong"
                answer["text"] = f"Synthetic {kind} answer {index}"
            if multi_select:
                question.update(type="multi_select", select_count=2)
                question["answers"].append(
                    {
                        "text": f"Second correct answer {index}",
                        "correct": True,
                        "rationale": "Second correct rationale",
                    }
                )
            questions.append(question)
        self.fixture_bank = v2_bank(
            questions, cert="REPORT-C01", source="report-fixtures"
        )
        path = self.root / "session-bank.json"
        path.write_text(json.dumps(self.fixture_bank), encoding="utf-8")
        import_internal_bank(self.conn, path)
        cert_id = self.conn.execute(
            "SELECT id FROM certifications WHERE code='REPORT-C01'"
        ).fetchone()[0]
        service = QuizService(QuizRepository(self.conn))
        session_id = service.create_session(
            cert_id,
            count=count,
            target_year=2026,
            mode="study",
            strategy="random",
            seed=seed,
        )
        return service, session_id, service.questions(session_id)

    def _export(self, session_id):
        bundle = self.reports.session_data(session_id)
        paths = write_report_bundle(bundle, self.root / "exports")
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self._assert_context_reconstructs_session(session_id, context)
        report = paths["report"].read_text(encoding="utf-8")
        prompt = paths["prompt"].read_text(encoding="utf-8").rstrip("\n")
        self.assertEqual(context["continuation_prompt"], prompt)
        self.assertEqual(
            report.split("```text\n", 1)[1].split("\n```", 1)[0], prompt
        )
        self.assertEqual(
            continuation_prompt(self.reports.session_data(session_id)), prompt
        )
        with (
            patch(
                "aws_study.cli._db",
                side_effect=lambda args: connect(self.root / "study.db"),
            ),
            redirect_stdout(StringIO()) as output,
        ):
            main(["prompt", str(session_id)])
        self.assertEqual(output.getvalue(), prompt + "\n")
        return bundle, context, report, prompt

    def _assert_context_reconstructs_session(self, session_id, context):
        originals = ReportRepository(self.conn).session_questions(session_id)
        self.assertEqual(len(context["questions"]), len(originals))
        for question, original in zip(context["questions"], originals):
            answers = {a["id"]: a for a in question["answers"]}
            self.assertEqual(len(answers), len(original.answers))
            self.assertEqual(
                len(question["sources"]), len(original.details["sources"])
            )
            for answer in answers.values():
                self.assertIsInstance(answer["is_correct"], bool)
            self.assertNotIn("selected_answers", question)
            self.assertNotIn("correct_answers", question)
            for field in (
                "content_fingerprint",
                "created_at",
                "certification_id",
            ):
                self.assertNotIn(field, question)
            self.assertEqual(
                set(question["selected_answer_ids"]),
                original.selected_answer_ids,
            )
            self.assertEqual(
                question["correct_answer_ids"],
                [a["id"] for a in original.answers if a["is_correct"]],
            )
            for source, raw in zip(
                question["sources"], original.details["sources"]
            ):
                self.assertTrue(
                    {
                        "source_key",
                        "name",
                        "source_ref",
                        "source_order",
                        "source_type",
                        "observed_year",
                        "verification_status",
                        "verified_year",
                        "verification_origin",
                        "valid_from_year",
                        "valid_to_year",
                    }
                    <= source.keys()
                )
                for field, value in source.items():
                    if field == "source_classification":
                        self.assertEqual(
                            value,
                            {
                                "area": raw["source_area"],
                                "topic": raw["source_topic"],
                            },
                        )
                    elif field != "answers":
                        self.assertEqual(value, raw[field])
                for field in ("id", "question_id", "source_id", "created_at"):
                    self.assertNotIn(field, source)
                self.assertEqual(len(source["answers"]), len(raw["answers"]))
                for ref, answer in zip(source["answers"], raw["answers"]):
                    full = answers[ref["answer_id"]]
                    self.assertEqual(full["id"], answer["id"])
                    self.assertEqual(
                        full["answer_text"], answer["answer_text"]
                    )
                    self.assertEqual(
                        ref["source_order"], answer["source_order"]
                    )
                    self.assertEqual(
                        ref.get("rationale", full["rationale"]),
                        answer["rationale"],
                    )
                    self.assertNotIn("answer_text", ref)
                    if answer["rationale"] == full["rationale"]:
                        self.assertNotIn("rationale", ref)

    def test_perfect_sessions_export_all_questions_and_saved_answer_order(
        self,
    ):
        for count in (10, 20):
            with self.subTest(count=count):
                service, session_id, questions = self._session(count)
                for position, question in enumerate(questions, start=1):
                    service.record_answer(
                        session_id,
                        question.id,
                        {o.id for o in question.options if o.correct},
                        "high",
                        position * 100,
                    )
                service.finish_session(session_id)
                bundle, context, report, prompt = self._export(session_id)
                self.assertEqual(context["schema_version"], 3)
                self.assertEqual(len(context["questions"]), count)
                self.assertEqual(
                    [q["id"] for q in context["questions"]],
                    [q.id for q in questions],
                )
                self.assertNotEqual(
                    [q.id for q in questions], sorted(q.id for q in questions)
                )
                for position, (record, original) in enumerate(
                    zip(context["questions"], questions), start=1
                ):
                    self.assertEqual(record["position"], position)
                    self.assertEqual(record["question_text"], original.text)
                    self.assertEqual(
                        record["result"],
                        {
                            "is_correct": True,
                            "confidence": "high",
                            "elapsed_ms": position * 100,
                        },
                    )
                    self.assertIs(record["result"]["is_correct"], True)
                    self.assertEqual(
                        [a["id"] for a in record["answers"]],
                        [o.id for o in original.options],
                    )
                    self.assertEqual(
                        [a["label"] for a in record["answers"]],
                        [o.label for o in original.options],
                    )
                    self.assertEqual(
                        [a["rationale"] for a in record["answers"]],
                        [o.rationale for o in original.options],
                    )
                    self.assertEqual(
                        record["selected_answer_ids"],
                        record["correct_answer_ids"],
                    )
                    self.assertNotIn(record["topic"], prompt)
                self.assertNotIn("## Reinforcement candidates", report)
                self.assertIn(f"Score: {count}/{count} (100.0%)", report)
                self.assertEqual(len(bundle["attempts"]), count)

    def test_mixed_session_priorities_no_answer_leak_and_shared_prompt(self):
        service, session_id, questions = self._session(6)
        states = [
            (True, "medium"),
            (False, "high"),
            (True, "low"),
            (True, "high"),
            (True, None),
            (False, None),
        ]
        # Record attempts backwards to distinguish attempt order from quiz order.
        for index in reversed(range(len(questions))):
            question = questions[index]
            correct, confidence = states[index]
            service.record_answer(
                session_id,
                question.id,
                {o.id for o in question.options if o.correct == correct},
                confidence,
                index * 100,
            )
        service.finish_session(session_id)
        bundle, context, report, prompt = self._export(session_id)
        self.assertEqual(
            [q["id"] for q in context["questions"]], [q.id for q in questions]
        )
        self.assertEqual(
            [a["question_id"] for a in bundle["attempts"]],
            [q.id for q in questions],
        )
        for index, record in enumerate(context["questions"]):
            self.assertEqual(record["result"]["is_correct"], states[index][0])
            self.assertEqual(record["result"]["confidence"], states[index][1])
            self.assertEqual(record["result"]["elapsed_ms"], index * 100)
            for answer in record["answers"]:
                self.assertNotIn(answer["answer_text"], prompt)
            self.assertNotIn(record["question_text"], prompt)
        topics = [q["topic"] for q in context["questions"]]
        positions = [prompt.index(topics[i]) for i in (1, 5, 2, 0)]
        self.assertEqual(positions, sorted(positions))
        for index in (3, 4):
            self.assertNotIn(topics[index], prompt)
        self.assertIn("report-fixtures #", prompt)
        self.assertIn(
            "Do not reveal or hint at answers from prior attempts before I respond.",
            prompt,
        )
        self.assertIn(
            "attached session context or local question bank", prompt
        )
        self.assertLess(len(prompt), 3000)
        section = report.split("## Reinforcement candidates", 1)[1].split(
            "## Compact ChatGPT", 1
        )[0]
        for index in (2, 0):
            self.assertIn(topics[index], section)
        for index in (1, 3, 4, 5):
            self.assertNotIn(topics[index], section)
        self.assertIn("Confidence: medium", section)
        for record in context["questions"]:
            for answer in record["answers"]:
                self.assertNotIn(answer["answer_text"], section)
        for index in (1, 5):
            for answer in context["questions"][index]["answers"]:
                self.assertIn(answer["answer_text"], report)
        filtered = continuation_prompt(bundle, include_low_confidence=False)
        self.assertNotIn(topics[2], filtered)
        self.assertIn(topics[0], filtered)

    def test_multiple_sources_keep_their_answers_and_rationales(self):
        service, session_id, questions = self._session(1, seed=1)
        duplicate = v2_bank(
            self.fixture_bank["questions"], cert="REPORT-C01", source="other"
        )
        duplicate["questions"][0]["answers"].reverse()
        duplicate["questions"][0]["answers"][0]["rationale"] = (
            "Other explanation"
        )
        path = self.root / "other-bank.json"
        path.write_text(json.dumps(duplicate), encoding="utf-8")
        import_internal_bank(self.conn, path)
        question = questions[0]
        service.record_answer(
            session_id,
            question.id,
            {o.id for o in question.options if o.correct},
            "low",
            1,
        )
        service.finish_session(session_id)
        _, context, _, _ = self._export(session_id)
        record = context["questions"][0]
        self.assertEqual(
            [s["source_key"] for s in record["sources"]],
            ["report-fixtures", "other"],
        )
        for source in record["sources"]:
            self.assertEqual(source["source_ref"], "1")
            self.assertEqual(len(source["answers"]), 2)
        self.assertEqual(
            record["sources"][1]["answers"][0]["rationale"],
            "Other explanation",
        )
        self.assertEqual(
            [a["id"] for a in record["answers"]],
            [o.id for o in question.options],
        )
        source_orders = [
            [a["answer_id"] for a in source["answers"]]
            for source in record["sources"]
        ]
        self.assertEqual(source_orders[0], list(reversed(source_orders[1])))
        self.assertNotEqual(
            source_orders[0], [a["id"] for a in record["answers"]]
        )

    def test_null_and_empty_source_rationales_override_displayed_text(self):
        service, session_id, questions = self._session(1)
        for key, rationale in (
            ("no-rationale", None),
            ("empty-rationale", ""),
        ):
            duplicate = copy.deepcopy(self.fixture_bank)
            duplicate["source"]["key"] = key
            duplicate["questions"][0]["answers"][0]["rationale"] = rationale
            path = self.root / f"{key}.json"
            path.write_text(json.dumps(duplicate), encoding="utf-8")
            import_internal_bank(self.conn, path)
        q = questions[0]
        service.record_answer(
            session_id,
            q.id,
            {o.id for o in q.options if not o.correct},
            "high",
            3,
        )
        service.finish_session(session_id)
        _, context, _, _ = self._export(session_id)
        sources = context["questions"][0]["sources"]
        self.assertNotIn("rationale", sources[0]["answers"][0])
        self.assertIn("rationale", sources[1]["answers"][0])
        self.assertIsNone(sources[1]["answers"][0]["rationale"])
        self.assertEqual(sources[2]["answers"][0]["rationale"], "")

    def test_bundle_renders_and_resolves_answers_without_live_database(self):
        bundle = self.reports.session_data(self.session_id)
        self.conn.close()
        paths = write_report_bundle(bundle, self.root / "offline")
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        question = context["questions"][0]
        answers = {a["id"]: a for a in question["answers"]}
        selected = answers[question["selected_answer_ids"][0]]
        correct = answers[question["correct_answer_ids"][0]]
        self.assertEqual(
            f"{selected['label']}. {selected['answer_text']}",
            self.selected_text,
        )
        self.assertEqual(
            f"{correct['label']}. {correct['answer_text']}", self.correct_text
        )
        self.assertIn("exact question wording and answers", context["purpose"])

    def test_multi_select_exports_complete_selected_and_correct_sets(self):
        service, session_id, questions = self._session(2, multi_select=True)
        for index, question in enumerate(questions):
            correct_ids = {o.id for o in question.options if o.correct}
            selected = (
                correct_ids
                if index == 0
                else {
                    next(o.id for o in question.options if not o.correct),
                    next(o.id for o in question.options if o.correct),
                }
            )
            service.record_answer(
                session_id, question.id, selected, "medium", 5
            )
        service.finish_session(session_id)
        _, context, _, _ = self._export(session_id)
        for index, (record, question) in enumerate(
            zip(context["questions"], questions)
        ):
            self.assertEqual(record["question_type"], "multi_select")
            self.assertEqual(record["select_count"], 2)
            self.assertEqual(len(record["selected_answer_ids"]), 2)
            self.assertEqual(len(record["correct_answer_ids"]), 2)
            self.assertEqual(record["result"]["is_correct"], index == 0)
            correct_ids = {o.id for o in question.options if o.correct}
            self.assertEqual(set(record["correct_answer_ids"]), correct_ids)
            selected_ids = set(record["selected_answer_ids"])
            self.assertEqual(selected_ids == correct_ids, index == 0)
            by_id = {a["id"]: a for a in record["answers"]}
            self.assertEqual(
                [
                    by_id[aid]["display_order"]
                    for aid in record["selected_answer_ids"]
                ],
                sorted(
                    by_id[aid]["display_order"]
                    for aid in record["selected_answer_ids"]
                ),
            )

    def test_unanswered_questions_remain_visible_and_do_not_count_as_misses(
        self,
    ):
        service, session_id, questions = self._session(3)
        for answered in (0, 1):
            with self.subTest(answered=answered):
                if answered:
                    question = questions[1]
                    service.record_answer(
                        session_id,
                        question.id,
                        {o.id for o in question.options if o.correct},
                        None,
                        0,
                    )
                    # Imported history can have no elapsed measurement.
                    self.conn.execute(
                        "UPDATE attempts SET elapsed_ms=NULL "
                        "WHERE session_id=:session_id",
                        {"session_id": session_id},
                    )
                    self.conn.commit()
                bundle, context, report, prompt = self._export(session_id)
                self.assertEqual(len(context["questions"]), 3)
                self.assertEqual(len(bundle["attempts"]), answered)
                self.assertIn(f"Answered: {answered}/3", report)
                self.assertIn(f"Incomplete session: {answered}/3", prompt)
                self.assertNotIn("MISSED", prompt)
                self.assertNotIn("## Reinforcement candidates", report)
                self.assertIn(
                    "Score: 1/1 (100.0%)" if answered else "Score: 0/0 (0.0%)",
                    report,
                )
                for index, record in enumerate(context["questions"]):
                    if answered and index == 1:
                        self.assertEqual(
                            record["result"],
                            {
                                "is_correct": True,
                                "confidence": None,
                                "elapsed_ms": None,
                            },
                        )
                    else:
                        self.assertIsNone(record["result"])
                        self.assertEqual(record["selected_answer_ids"], [])
                    self.assertTrue(record["correct_answer_ids"])
                    self.assertTrue(record["answers"])

    def test_session_queries_are_batched_and_do_not_write(self):
        service, session_id, _ = self._session(20)
        before = self.conn.serialize()
        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            bundle = self.reports.session_data(session_id)
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(len(bundle["questions"]), 20)
        self.assertLessEqual(len(statements), 6)
        self.assertEqual(self.conn.serialize(), before)

    def test_missing_saved_answer_order_is_reported_instead_of_invented(self):
        _, session_id, _ = self._session(1)
        self.conn.execute(
            "DELETE FROM session_answers WHERE session_id=:session_id",
            {"session_id": session_id},
        )
        with self.assertRaisesRegex(ValueError, "has no saved answer order"):
            self.reports.session_data(session_id)

    def test_session_with_no_saved_questions_exports_empty_context(self):
        self.conn.execute(
            "INSERT INTO sessions(certification_id,mode) VALUES (:certification_id,'study')",
            {"certification_id": self.cert_id},
        )
        session_id = self.conn.execute(
            "SELECT MAX(id) FROM sessions"
        ).fetchone()[0]
        self.conn.commit()
        _, context, report, prompt = self._export(session_id)
        self.assertEqual(context["questions"], [])
        self.assertIn("Answered: 0/0", report)
        self.assertIn("No answered questions", prompt)


if __name__ == "__main__":
    unittest.main()
