import copy
import json
import sqlite3
from dataclasses import asdict

from study_fixture import BankTestCase, question

from aws_study.bank_schema import BankValidationError
from aws_study.import_models import ImportConflict
from aws_study.importers import import_internal_bank, preview_bank
from aws_study.quiz_repository import QuizRepository
from aws_study.report_repository import ReportRepository
from aws_study.source_repository import SourceRepository
from aws_study.source_service import SourceService


class ImportTests(BankTestCase):
    def test_reimport_is_idempotent_and_preserves_answer_ids(self):
        first = self.import_questions([question()])
        ids = [r[0] for r in self.conn.execute("SELECT id FROM answers")]
        second = self.import_questions([question()])
        self.assertEqual(first.new_canonical_questions, 1)
        self.assertEqual(second.new_canonical_questions, 0)
        self.assertEqual(second.new_provenance_links, 0)
        self.assertEqual(
            ids, [r[0] for r in self.conn.execute("SELECT id FROM answers")]
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT fingerprint_version FROM questions"
            ).fetchone()[0],
            1,
        )

    def test_multiple_sources_merge_with_order_and_rationale(self):
        self.import_questions([question()])
        second = question()
        second["answers"].reverse()
        second["answers"][0]["rationale"] = "Different source explanation"
        result = self.import_questions([second], source="other")
        self.assertEqual(result.new_canonical_questions, 0)
        self.assertEqual(result.new_provenance_links, 1)
        sources = ReportRepository(self.conn).provenance_for_questions({1})[1]
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]["answers"][0]["answer_text"], "Amazon S3")
        self.assertEqual(
            sources[1]["answers"][0]["rationale"],
            "Different source explanation",
        )
        self.assertEqual(
            len(
                QuizRepository(self.conn).candidate_history(
                    1, target_year=2026
                )
            ),
            1,
        )

    def test_repeated_source_content_keeps_refs_without_duplicate_candidates(
        self,
    ):
        second = question()
        second["source_ref"] = "2"
        self.import_questions([question(), second])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM question_sources"
            ).fetchone()[0],
            2,
        )
        self.assertEqual(
            len(
                QuizRepository(self.conn).candidate_history(
                    1, target_year=2026
                )
            ),
            1,
        )

    def test_conflicts_roll_back_and_identify_existing_and_incoming(self):
        self.import_questions([question()])
        for field in ("key", "classification", "content"):
            changed = question()
            if field == "key":
                for a in changed["answers"]:
                    a["correct"] = not a["correct"]
            elif field == "classification":
                changed["classification"]["area"] = "Networking"
            else:
                changed["question"] = "Changed content"
            with (
                self.subTest(field=field),
                self.assertRaises(ImportConflict) as error,
            ):
                self.import_questions([changed])
            self.assertIn("question", str(error.exception).lower())
            self.assertEqual(
                self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[
                    0
                ],
                1,
            )
            self.assertEqual(
                self.conn.execute("SELECT area FROM questions").fetchone()[0],
                "Compute",
            )

    def test_invalid_records_do_not_write_anything(self):
        changes = [
            lambda q: q.update(type="unknown"),
            lambda q: q.update(select_count=True),
            lambda q: q["answers"][0].update(correct=True),
            lambda q: q["answers"][0].update(text="  "),
            lambda q: q["answers"][0].update(text=" AMAZON S3 "),
            lambda q: q["answers"][0].update(correct="false"),
            lambda q: q.update(type="multi_select", select_count=2),
            lambda q: q.update(classification={}),
            lambda q: q.update(confidence="high"),
        ]
        for change in changes:
            q = question(2)
            change(q)
            with self.assertRaises(BankValidationError):
                self.import_questions([question(), q])
            self.assertEqual(
                self.conn.execute(
                    "SELECT COUNT(*) FROM certifications"
                ).fetchone()[0],
                0,
            )

    def test_late_database_failure_rolls_back(self):
        self.conn.execute(
            """CREATE TRIGGER reject_second BEFORE INSERT ON questions
            WHEN NEW.question_text='Synthetic question 2?'
            BEGIN SELECT RAISE(ABORT, 'Synthetic failure'); END"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.import_questions([question(), question(2)])
        for table in (
            "certifications",
            "sources",
            "questions",
            "answers",
            "question_sources",
        ):
            self.assertEqual(
                self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                    0
                ],
                0,
            )

    def test_legacy_input_is_explicitly_rejected(self):
        path = self.root / "legacy.json"
        path.write_text(json.dumps({"schema_version": 1, "questions": []}))
        with self.assertRaisesRegex(
            BankValidationError, "Only schema_version 2"
        ):
            import_internal_bank(self.conn, path)

    def test_manual_verification_survives_reimport_and_new_occurrence(self):
        self.import_questions([question()])
        SourceService(SourceRepository(self.conn)).verify(
            1, "TEST-C01", "pretest", year=2027, status="verified_current"
        )
        self.import_questions([question(), question(2)])
        rows = self.conn.execute(
            "SELECT verified_year,verification_status FROM question_sources"
        ).fetchall()
        self.assertEqual(
            [tuple(r) for r in rows], [(2027, "verified_current")] * 2
        )

    def test_one_fresh_source_keeps_canonical_candidate_eligible(self):
        self.import_questions([question()])
        self.import_questions(
            [question()],
            source="other",
            observed_year=2026,
            verified_year=2027,
        )
        repository = QuizRepository(self.conn)
        self.assertEqual(
            len(repository.candidate_history(1, target_year=2027)), 1
        )
        self.assertEqual(repository.candidate_history(1, target_year=2028), ())

    def test_curated_ec2_classification_ignores_on_premises_text(self):
        q = question()
        q["question"] = (
            "Synthetic company migrates its on-premises application to Amazon EC2 with predictable usage. Which purchasing option?"
        )
        q["classification"] = {
            "area": "Compute",
            "topic": "EC2 purchasing options",
        }
        self.import_questions([q])
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "SELECT area,topic FROM questions"
                ).fetchone()
            ),
            ("Compute", "EC2 purchasing options"),
        )

    def test_preview_matches_import_without_modifying_database(self):
        self.import_questions([question()])
        path = self.root / "bank.json"
        self.conn.commit()
        before = self.conn.serialize()
        preview = preview_bank(self.conn, path)
        self.assertEqual(self.conn.serialize(), before)
        actual = import_internal_bank(self.conn, path)
        self.assertEqual(asdict(preview), asdict(actual))

    def test_missing_refs_match_content_even_if_reordered(self):
        qs = [question(1), question(2)]
        for q in qs:
            q.pop("source_ref")
        self.import_questions(qs)
        again = self.import_questions(list(reversed(qs)))
        self.assertEqual(again.new_provenance_links, 0)
        self.assertEqual(again.new_canonical_questions, 0)

    def test_conflicting_multi_select_count_and_type_are_reported(self):
        q = question()
        q["answers"].append({"text": "Third choice", "correct": True})
        q.update(type="multi_select", select_count=2)
        self.import_questions([q])
        changed = copy.deepcopy(q)
        changed["answers"][0]["correct"] = True
        changed["select_count"] = 3
        with self.assertRaisesRegex(ImportConflict, "select_count"):
            self.import_questions([changed], source="other")
        changed = copy.deepcopy(q)
        changed.update(type="single_select", select_count=1)
        changed["answers"][2]["correct"] = False
        with self.assertRaisesRegex(ImportConflict, "question_type"):
            self.import_questions([changed], source="other")

    def test_source_ref_conflict_rolls_back_new_question_and_other_records(
        self,
    ):
        self.import_questions([question()])
        changed = question()
        changed["answers"][0]["text"] = "Replacement distractor"
        with self.assertRaisesRegex(ImportConflict, "Source-content-change"):
            self.import_questions([question(2), changed])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM question_sources"
            ).fetchone()[0],
            1,
        )
