import json
import tempfile
import unittest
from contextlib import chdir, redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from aws_study.cli import main
from aws_study.db import connect, init_db
from aws_study.importers import import_internal_bank
from aws_study.quiz import run_quiz
from aws_study.reporting import write_report_bundle


class ReportingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "study.db")
        init_db(self.conn)
        bank = {
            "schema_version": 1,
            "questions": [{
                "source": "synthetic",
                "source_index": 1,
                "question": "Synthetic report test?",
                "question_type": "single_select",
                "dedup_role": "canonical",
                "options": [
                    {"letter": "A", "label": "Wrong", "correct": False,
                     "selected": False, "rationale": "Wrong explanation"},
                    {"letter": "B", "label": "Right", "correct": True,
                     "selected": False, "rationale": "Right explanation"},
                ],
            }],
        }
        bank_path = self.root / "bank.json"
        bank_path.write_text(json.dumps(bank), encoding="utf-8")
        import_internal_bank(
            self.conn, bank_path, cert_code="CLF-C02", observed_year=2026,
            verified_year=2026, import_baseline_attempts=False,
        )
        self.cert_id = self.conn.execute(
            "SELECT id FROM certifications",
        ).fetchone()[0]
        with (
            patch("builtins.input", side_effect=["a", "u"]),
            redirect_stdout(StringIO()),
        ):
            self.session_id = run_quiz(
                self.conn, self.cert_id, count=1, target_year=2026,
                mode="exam", strategy="random", seed=1,
            )
        self.clock = patch("aws_study.reporting.datetime")
        self.mock_datetime = self.clock.start()
        self.mock_datetime.now.return_value = datetime(2026, 10, 6, 14, 30, 12)
        self.addCleanup(self.clock.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_named_folder_contains_complete_bundle(self):
        parent = self.root / "exports"
        paths = write_report_bundle(self.conn, self.session_id, parent)
        folder = parent / "CLF-C02" / "20261006-143012"
        self.assertEqual({path.parent for path in paths.values()}, {folder})
        self.assertEqual({path.name for path in folder.iterdir()},
                         {f"session-{self.session_id}-{suffix}" for suffix in
                          ("report.md", "prompt.txt", "context.json")})
        report = paths["report"].read_text(encoding="utf-8")
        self.assertIn("Score: 0/1 (0.0%)", report)
        self.assertIn("Selected: A. Wrong", report)
        self.assertIn("Correct: B. Right", report)
        prompt = paths["prompt"].read_text(encoding="utf-8")
        self.assertIn("AWS CLF-C02", prompt)
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertEqual(context["session"]["id"], self.session_id)
        self.assertEqual(context["continuation_prompt"], prompt.rstrip("\n"))
        self.assertEqual(
            context["questions"][0]["question_text"], "Synthetic report test?",
        )

    def test_same_second_exports_preserve_previous_files(self):
        parent = self.root / "exports"
        first = write_report_bundle(self.conn, self.session_id, parent)
        first["report"].write_text("Previous report", encoding="utf-8")
        second = write_report_bundle(self.conn, self.session_id, parent)
        self.assertNotEqual(first["report"].parent, second["report"].parent)
        self.assertEqual(second["report"].parent.name,
                         "20261006-143012-2")
        self.assertEqual(
            first["report"].read_text(encoding="utf-8"), "Previous report",
        )
        self.assertIn(
            "Score: 0/1", second["report"].read_text(encoding="utf-8"),
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
            self.conn, self.session_id, self.root / "exports",
        )
        self.assertEqual(
            paths["report"].parent,
            self.root / "exports" / "SAA-C03" / "20261006-143012",
        )
        context = json.loads(paths["context"].read_text(encoding="utf-8"))
        self.assertEqual(context["session"]["target_year"], 2027)
        self.assertIn(
            "Target year: 2027", paths["report"].read_text(encoding="utf-8"),
        )
        self.conn.execute(
            "UPDATE sessions SET target_year=NULL WHERE id=?",
            (self.session_id,),
        )
        self.conn.commit()
        paths = write_report_bundle(
            self.conn, self.session_id, self.root / "exports",
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
        paths = write_report_bundle(self.conn, self.session_id, parent)
        self.assertEqual(paths["report"].parent.parent.parent, parent)
        self.assertEqual(paths["report"].parent.parent.name, "odd-cert-CLF-C02")

    def test_quiz_and_report_commands_use_new_default_parent(self):
        with (
            chdir(self.root),
            patch("aws_study.cli._db", return_value=self.conn),
        ):
            with redirect_stdout(StringIO()) as output:
                main(["report", str(self.session_id)])
            expected = Path(
                "private/reports/CLF-C02/20261006-143012",
            ) / f"session-{self.session_id}-report.md"
            self.assertTrue(expected.is_file())
            self.assertIn(str(expected), output.getvalue())
            with (
                patch("builtins.input", side_effect=["B", "C"]),
                redirect_stdout(StringIO()),
            ):
                main([
                    "quiz", "--cert", "CLF-C02", "--year", "2026", "-n", "1",
                ])
            folders = list(Path("private/reports/CLF-C02").iterdir())
            self.assertEqual(len(folders), 2)
            self.assertTrue(all(
                len(list(folder.glob("session-*-report.md"))) == 1
                for folder in folders
            ))
            self.assertFalse(Path("private/report").exists())


if __name__ == "__main__":
    unittest.main()
