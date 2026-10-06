import copy
import json
import sqlite3
import unittest

from aws_study.importers import import_internal_bank
from study_fixture import BankTestCase, question


class ImportTests(BankTestCase):
    def test_reimport_preserves_ids_history_and_metadata(self):
        first = self.import_questions([question()])
        record = dict(self.conn.execute("SELECT * FROM questions").fetchone())
        options = [row[0] for row in self.conn.execute(
            "SELECT id FROM options ORDER BY option_order",
        )]
        baseline = dict(self.conn.execute("SELECT * FROM attempts").fetchone())
        self.assertEqual(first.questions_inserted, 1)
        self.assertEqual(first.baseline_attempts, 1)
        self.assertEqual(baseline["confidence"], "medium")
        self.assertEqual(baseline["elapsed_ms"], 1500)
        self.assertEqual(baseline["is_correct"], 0)
        self.assertEqual(json.loads(record["metadata_json"]), {
            "original_status": "Incorrect", "original_qtype": None,
            "original_confidence": "Educated guess",
            "time_to_answer_seconds": 1.5, "import_schema_version": 7,
        })
        replacement = question()
        replacement["options"][1]["rationale"] = "Revised rationale"
        replacement["original_confidence"] = "Confident"
        replacement["status"] = "Correct"
        second = self.import_questions([replacement], verified_year=2027)
        self.assertEqual(second.questions_updated, 1)
        self.assertEqual(second.questions_inserted, 0)
        self.assertEqual(second.baseline_attempts, 0)
        self.assertEqual(second.exact_duplicate_hashes, 0)
        stored = self.conn.execute("SELECT * FROM questions").fetchone()
        self.assertEqual(stored["id"], record["id"])
        self.assertEqual(stored["verified_year"], 2027)
        self.assertEqual(stored["valid_from_year"], 2026)
        self.assertEqual(options, [row[0] for row in self.conn.execute(
            "SELECT id FROM options ORDER BY option_order",
        )])
        self.assertEqual(dict(self.conn.execute(
            "SELECT * FROM attempts",
        ).fetchone()), baseline)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM tags",
        ).fetchone()[0], 3)
        selected = self.conn.execute(
            "SELECT option_id FROM attempt_options",
        ).fetchone()[0]
        self.assertEqual(selected, options[0])
        self.assertEqual(self.conn.execute(
            "SELECT verified_at FROM sources",
        ).fetchone()[0], "2027-01-01")

    def test_duplicate_content_and_baselines_are_scoped_to_sources(self):
        alternate = question(source="practice_exam")
        alternate["dedup_role"] = "alternate"
        summary = self.import_questions([question(), alternate])
        self.assertEqual(summary.questions_inserted, 2)
        self.assertEqual(summary.exact_duplicate_hashes, 1)
        self.assertEqual(summary.baseline_attempts, 2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sessions",
        ).fetchone()[0], 2)
        again = self.import_questions([question(), alternate])
        self.assertEqual(again.questions_updated, 2)
        self.assertEqual(again.baseline_attempts, 0)
        self.assertEqual(again.exact_duplicate_hashes, 2)

    def test_disabled_baselines_and_top_level_array(self):
        path = self.root / "array.json"
        path.write_text(json.dumps([question()]), encoding="utf-8")
        summary = import_internal_bank(
            self.conn, path, cert_code="TEST-C01",
            import_baseline_attempts=False,
        )
        self.assertEqual(summary.questions_inserted, 1)
        self.assertEqual(summary.baseline_attempts, 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sessions",
        ).fetchone()[0], 0)
        self.assertIsNone(self.conn.execute(
            "SELECT verified_year FROM questions",
        ).fetchone()[0])

    def test_late_sql_failure_rolls_back_every_part_of_new_bank(self):
        self.conn.execute("""
            CREATE TRIGGER reject_second BEFORE INSERT ON questions
            WHEN NEW.external_key='2'
            BEGIN SELECT RAISE(ABORT, 'Synthetic import failure'); END
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "import failure"):
            self.import_questions([question(), question(2)])
        for table in (
            "certifications", "sources", "questions", "options", "tags",
            "question_tags", "sessions", "session_questions", "attempts",
            "attempt_options",
        ):
            with self.subTest(table=table):
                self.assertEqual(self.conn.execute(
                    f"SELECT COUNT(*) FROM {table}",
                ).fetchone()[0], 0)
        self.assertFalse(self.conn.in_transaction)

    def test_failed_reimport_preserves_existing_content_and_provenance(self):
        original = question()
        self.import_questions([original])
        before = {
            table: [tuple(row) for row in self.conn.execute(
                f"SELECT * FROM {table}",
            )]
            for table in ("certifications", "sources", "questions", "options")
        }
        replacement = copy.deepcopy(original)
        replacement["question"] = "Changed synthetic question?"
        with self.assertRaisesRegex(ValueError, "Question 2"):
            self.import_questions(
                [replacement, {}], cert_name="Changed", verified_year=2027,
            )
        for table, rows in before.items():
            with self.subTest(table=table):
                self.assertEqual([tuple(row) for row in self.conn.execute(
                    f"SELECT * FROM {table}",
                )], rows)

    def test_malformed_options_roll_back_the_import(self):
        for options in ({"label": "Wrong shape"}, ["Not an option object"]):
            with self.subTest(options=options):
                malformed = question(2)
                malformed["options"] = options
                with self.assertRaisesRegex(ValueError, "Question options"):
                    self.import_questions([question(), malformed])
                self.assertEqual(self.conn.execute(
                    "SELECT COUNT(*) FROM questions",
                ).fetchone()[0], 0)
                self.assertFalse(self.conn.in_transaction)


if __name__ == "__main__":
    unittest.main()
