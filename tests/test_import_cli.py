"""CLI preview and validation must leave persisted state alone."""

import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO

from study_fixture import BankTestCase, bank, question

from aws_study.cli import main


class ImportCliTests(BankTestCase):
    def run_cli(self, args, *, failed=False):
        with (
            redirect_stdout(StringIO()) as out,
            redirect_stderr(StringIO()) as err,
        ):
            if failed:
                with self.assertRaises(SystemExit) as error:
                    main(args)
                self.assertEqual(error.exception.code, 2)
            else:
                main(args)
        return out.getvalue(), err.getvalue()

    def write_bank(self, data):
        path = self.root / "cli-bank.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return str(path)

    def test_preview_absent_database_creates_no_file_or_directory(self):
        path = self.write_bank(bank([question()]))
        missing = self.root / "absent" / "new.db"
        out, err = self.run_cli(
            ["--db", str(missing), "import-json", path, "--dry-run"]
        )
        self.assertEqual(json.loads(out)["new_canonical_questions"], 1)
        self.assertEqual(err, "")
        self.assertFalse(missing.parent.exists())

    def test_invalid_import_does_not_create_database(self):
        q = question()
        q["select_count"] = 4
        path = self.write_bank(bank([q]))
        dest = self.root / "absent" / "new.db"
        _, err = self.run_cli(
            ["--db", str(dest), "import-json", path], failed=True
        )
        self.assertIn("select_count", err)
        self.assertFalse(dest.parent.exists())

    def test_preview_reports_all_invalid_records_and_exits_two(self):
        first, second = question(1), question(2)
        first["question"] = ""
        second["answers"] = []
        path = self.write_bank(bank([first, second]))
        out, _ = self.run_cli(
            [
                "--db",
                str(self.root / "study.db"),
                "import-json",
                path,
                "--dry-run",
            ],
            failed=True,
        )
        summary = json.loads(out)
        self.assertEqual(summary["questions_seen"], 2)
        self.assertEqual(len(summary["invalid_records"]), 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            0,
        )

    def test_preview_reports_conflicting_existing_question_without_writes(
        self,
    ):
        self.import_questions([question()])
        q = question()
        for a in q["answers"]:
            a["correct"] = not a["correct"]
        path = self.write_bank(bank([q], source="other"))
        before = self.conn.serialize()
        out, _ = self.run_cli(
            [
                "--db",
                str(self.root / "study.db"),
                "import-json",
                path,
                "--dry-run",
            ],
            failed=True,
        )
        self.assertIn(
            "answer_key_fingerprint", json.loads(out)["conflicts"][0]
        )
        self.assertEqual(self.conn.serialize(), before)

    def test_import_uses_file_metadata_without_legacy_flags(self):
        path = self.write_bank(
            bank([question()], source="cli-source", cert="CLI-C01")
        )
        out, _ = self.run_cli(
            ["--db", str(self.root / "study.db"), "import-json", path]
        )
        self.assertEqual(json.loads(out)["source"], "cli-source")
        self.assertEqual(
            self.conn.execute("SELECT code FROM certifications").fetchone()[0],
            "CLI-C01",
        )
