"""One generic importer preserves provenance, trust, and immutable history."""

import copy
import json
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO

from converter_fixture import external_question, replace_domain, write_corpus
from study_fixture import BankTestCase, bank, question

from aws_study.bank_schema import BankValidationError, validate_bank
from aws_study.bank_serialization import bank_json, bank_mapping
from aws_study.cli import main
from aws_study.converters.cloudcertprep import convert_corpus
from aws_study.import_models import ImportConflict
from aws_study.importers import import_bank, preview_bank
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.report_repository import ReportRepository
from aws_study.report_service import ReportService
from aws_study.reporting import continuation_prompt
from aws_study.source_repository import SourceRepository
from aws_study.source_service import SourceService


class SourceSnapshotTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.input = self.root / "upstream"
        write_corpus(self.input)
        self.sources = SourceRepository(self.conn)
        self.repo = QuizRepository(self.conn)
        self.service = QuizService(self.repo)

    def import_snapshot(self, data, *, old=None):
        return import_bank(
            self.conn, data, filename="generated.json", supersede_source=old
        )

    def eligible(self, *, year=2026, unverified=False):
        return {
            q.question_id
            for q in self.repo.candidate_history(
                1, target_year=year, include_unverified=unverified
            )
        }

    def test_question_explanation_dates_metadata_and_manual_override(self):
        raw = external_question()
        raw.update(
            lastVerified="2026-06-15",
            taskStatement="1.1",
            services=["Amazon EC2"],
        )
        replace_domain(self.input, 1, [raw])
        data = convert_corpus(self.input).bank
        first = self.import_snapshot(data)
        answer_ids = list(self.conn.execute("SELECT id FROM answers"))
        source = self.conn.execute(
            "SELECT * FROM question_sources WHERE question_id=1"
        ).fetchone()
        self.assertEqual(source["explanation"], raw["explanation"])
        self.assertEqual(source["verified_at"], "2026-06-15")
        self.assertEqual(source["verified_year"], 2026)
        self.assertEqual(
            json.loads(source["metadata_json"])["services"], ["Amazon EC2"]
        )
        self.assertEqual(
            source["metadata_json"],
            json.dumps(
                json.loads(source["metadata_json"]),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        self.assertEqual(self.eligible(), {1})
        self.assertEqual(self.eligible(year=2027), set())
        self.assertTrue(
            all(
                r[0] is None
                for r in self.conn.execute(
                    "SELECT rationale FROM answers UNION ALL SELECT rationale FROM question_source_answers"
                )
            )
        )
        second = self.import_snapshot(data)
        self.assertEqual(first.new_provenance_links, 4)
        self.assertEqual(second.new_provenance_links, 0)
        self.assertEqual(
            list(self.conn.execute("SELECT id FROM answers")), answer_ids
        )
        SourceService(self.sources).verify(
            1, "CLF-C02", data.source.key, year=2027, status="verified_current"
        )
        self.import_snapshot(data)
        self.assertEqual(self.eligible(year=2027), {1, 2, 3, 4})
        source = self.conn.execute(
            "SELECT * FROM question_sources WHERE question_id=1"
        ).fetchone()
        self.assertEqual(source["verified_year"], 2027)
        self.assertEqual(source["verified_at"], "2026-06-15")
        with self.assertRaisesRegex(ValueError, "official status"):
            SourceService(self.sources).verify(
                1,
                "CLF-C02",
                data.source.key,
                year=2027,
                status="official_current",
            )

    def test_cross_source_classification_order_and_rationales(self):
        official = question()
        self.import_questions(
            [official], source="official", cert_code="CLF-C02"
        )
        raw = external_question()
        raw.update(
            question=official["question"],
            options={
                "A": official["answers"][1]["text"],
                "B": official["answers"][0]["text"],
            },
            answer="A",
            taskStatement="1.2",
        )
        replace_domain(self.input, 1, [raw])
        result = self.import_snapshot(convert_corpus(self.input).bank)
        self.assertEqual(result.existing_canonical_matches, 1)
        self.assertEqual(result.new_canonical_questions, 3)
        canonical = self.conn.execute(
            "SELECT area,topic FROM questions WHERE id=1"
        ).fetchone()
        self.assertEqual(
            tuple(canonical),
            (
                official["classification"]["area"],
                official["classification"]["topic"],
            ),
        )
        sources = ReportRepository(self.conn).provenance_for_questions({1})[1]
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[0]["source_area"], canonical[0])
        self.assertEqual(sources[1]["source_area"], "Cloud Concepts")
        self.assertEqual(sources[1]["source_topic"], "1.2")
        self.assertEqual(
            sources[0]["answers"][0]["rationale"],
            official["answers"][0]["rationale"],
        )
        self.assertEqual(
            sources[1]["answers"][0]["answer_text"], raw["options"]["A"]
        )
        changed = copy.deepcopy(official)
        changed["classification"] = {
            "area": "Different source area",
            "topic": "Different source topic",
        }
        self.import_questions(
            [changed], source="second-official", cert_code="CLF-C02"
        )
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "SELECT area,topic FROM questions WHERE id=1"
                ).fetchone()
            ),
            tuple(canonical),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT source_area FROM question_sources WHERE source_id=3"
            ).fetchone()[0],
            "Different source area",
        )

    def test_new_question_domain_only_and_optional_provenance(self):
        data = convert_corpus(self.input).bank
        self.import_snapshot(data)
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "SELECT area,topic FROM questions WHERE id=1"
                ).fetchone()
            ),
            ("Cloud Concepts", None),
        )
        later = bank([question()], source="other", cert="CLF-C02")
        self.import_snapshot(validate_bank(later))
        source = self.conn.execute(
            "SELECT explanation,verified_at,metadata_json,verified_year FROM question_sources WHERE source_id=2"
        ).fetchone()
        self.assertEqual(tuple(source), (None, None, None, 2026))

    def test_full_context_retains_explanation_without_prompt_leakage(self):
        raw = external_question()
        raw["explanation"] = "PRIVATE SYNTHETIC QUESTION EXPLANATION"
        raw["lastVerified"] = "2026-06-15"
        replace_domain(self.input, 1, [raw])
        self.import_snapshot(convert_corpus(self.input).bank)
        sid = self.service.create_session(
            1,
            count=4,
            target_year=2026,
            mode="study",
            strategy="random",
            seed=1,
            include_unverified=True,
        )
        for item in self.service.questions(sid):
            self.service.record_answer(
                sid,
                item.id,
                {next(o.id for o in item.options if not o.correct)},
                "low",
                100,
            )
        self.service.finish_session(sid)
        prepared = ReportService(ReportRepository(self.conn)).session_data(sid)
        first = next(q for q in prepared["questions"] if q["id"] == 1)
        source = first["sources"][0]
        self.assertEqual(source["explanation"], raw["explanation"])
        self.assertEqual(source["verified_at"], "2026-06-15")
        self.assertEqual(
            source["source_classification"],
            {"area": "Cloud Concepts", "topic": None},
        )
        self.assertIn("license_notice", source["metadata"])
        self.assertEqual(json.dumps(first).count(raw["explanation"]), 1)
        self.assertNotIn(raw["explanation"], continuation_prompt(prepared))

    def test_explicit_replacement_preserves_history_drafts_and_eligibility(
        self,
    ):
        first = convert_corpus(self.input).bank
        self.import_snapshot(first)
        SourceService(self.sources).verify(
            1,
            "CLF-C02",
            first.source.key,
            year=2026,
            status="verified_current",
        )
        sid = self.service.create_session(
            1,
            count=4,
            target_year=2026,
            mode="study",
            strategy="random",
            seed=1,
        )
        for item in self.service.questions(sid):
            self.service.record_answer(
                sid,
                item.id,
                {o.id for o in item.options if o.correct},
                "high",
                100,
            )
        self.service.finish_session(sid)
        draft = self.service.create_session(
            1,
            count=4,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=2,
        )
        item = self.service.questions(draft)[0]
        self.service.save_response(
            draft, item.id, {o.id for o in item.options if o.correct}, 50
        )
        self.service.toggle_flag(draft, item.id)
        before = self.service.resume_session(draft)
        history = {
            table: [
                tuple(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in (
                "attempts",
                "attempt_options",
                "session_answers",
                "answers",
                "sessions",
            )
        }
        old_answer_ids = history["answers"]
        raw = external_question()
        raw.update(
            question="Changed synthetic question?", lastVerified="2026-06-15"
        )
        replace_domain(self.input, 1, [raw])
        second = convert_corpus(self.input).bank
        with self.assertRaisesRegex(ImportConflict, "supersede-source"):
            self.import_snapshot(second)
        path = self.root / "second.json"
        path.write_text(bank_json(second))
        dump = list(self.conn.iterdump())
        preview = preview_bank(
            self.conn, path, supersede_source=first.source.key
        )
        self.assertEqual(preview.new_canonical_questions, 1)
        self.assertEqual(preview.existing_canonical_matches, 3)
        self.assertEqual(preview.superseded_sources, [first.source.key])
        self.assertEqual(list(self.conn.iterdump()), dump)
        actual = self.import_snapshot(second, old=first.source.key)
        self.assertEqual(actual, preview)
        self.assertEqual(self.service.resume_session(draft), before)
        for table, rows in history.items():
            after = [
                tuple(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            self.assertEqual(
                after[: len(rows)] if table == "answers" else after, rows
            )
        self.assertEqual(self.eligible(), {5})
        exported = ReportService(ReportRepository(self.conn)).session_data(sid)
        self.assertEqual(
            exported["questions"][0]["sources"][0]["superseded_by_source_key"],
            second.source.key,
        )
        self.assertEqual(self.eligible(unverified=True), {2, 3, 4, 5})
        self.import_snapshot(first)
        self.import_snapshot(second, old=first.source.key)
        self.assertEqual(self.eligible(unverified=True), {2, 3, 4, 5})
        self.assertEqual(
            [
                tuple(r)
                for r in self.conn.execute("SELECT * FROM answers ORDER BY id")
            ][: len(old_answer_ids)],
            old_answer_ids,
        )
        with self.assertRaisesRegex(ValueError, "cannot be reactivated"):
            SourceService(self.sources).verify(
                1,
                "CLF-C02",
                first.source.key,
                year=2026,
                status="verified_current",
            )
        # Retiring one source must not exclude a question supported elsewhere.
        source = bank(
            [
                {
                    "question": first.questions[0].text,
                    "type": "single_select",
                    "select_count": 1,
                    "classification": {
                        "area": "Curated",
                        "topic": "Other source",
                    },
                    "answers": [
                        {"text": a.text, "correct": a.correct}
                        for a in first.questions[0].answers
                    ],
                }
            ],
            source="independent",
            cert="CLF-C02",
        )
        self.import_snapshot(validate_bank(source))
        self.assertEqual(self.eligible(), {1, 5})

    def test_conflicting_replacement_and_immutable_snapshot_roll_back(self):
        first = convert_corpus(self.input).bank
        self.import_snapshot(first)
        original = list(self.conn.iterdump())
        changed = bank_mapping(first)
        changed["questions"][0]["explanation"] = (
            "Different explanation under same identity"
        )
        with self.assertRaisesRegex(ImportConflict, "Immutable snapshot"):
            self.import_snapshot(validate_bank(changed))
        self.assertEqual(list(self.conn.iterdump()), original)
        raw = external_question()
        raw["answer"] = "B"
        replace_domain(self.input, 1, [raw])
        second = convert_corpus(self.input).bank
        with self.assertRaisesRegex(ImportConflict, "answer_key_fingerprint"):
            self.import_snapshot(second, old=first.source.key)
        self.assertEqual(list(self.conn.iterdump()), original)
        for old in ("missing", first.source.key):
            with self.assertRaises(ImportConflict):
                self.import_snapshot(first, old=old)
        self.assertEqual(list(self.conn.iterdump()), original)
        plain = validate_bank(
            bank([question()], source="plain", cert="CLF-C02")
        )
        with self.assertRaisesRegex(ImportConflict, "requires a snapshot"):
            self.import_snapshot(plain, old=first.source.key)
        self.import_snapshot(plain)
        changed_type = replace(
            plain,
            source=replace(
                plain.source,
                kind="third_party",
                verification_status="unverified",
            ),
        )
        with self.assertRaisesRegex(
            ImportConflict, "Source type cannot change"
        ):
            self.import_snapshot(changed_type)
        third = replace(
            first,
            source=replace(
                first.source, key="other-family", snapshot_family="other"
            ),
        )
        with self.assertRaisesRegex(
            ImportConflict, "same certification and family"
        ):
            self.import_snapshot(third, old=first.source.key)

    def test_standard_optional_field_validation(self):
        data = bank_mapping(convert_corpus(self.input).bank)
        invalid_questions = [
            {"explanation": []},
            {"verified_at": "2026-02-30"},
            {"verified_at": "20260615"},
            {"verification_status": "unknown"},
            {"source_classification": []},
            {"source_classification": {"area": " "}},
            {"source_classification": {"area": "Domain", "topic": " "}},
            {"source_metadata": []},
            {"source_metadata": {"nested": {"confidence": "high"}}},
            {"source_metadata": {"value": float("nan")}},
            {"source_metadata": {1: "invalid key"}},
            {"source_metadata": {"bad": object()}},
            {"verification_status": "official_current"},
            {"classification": {}},
        ]
        for fields in invalid_questions:
            changed = copy.deepcopy(data)
            changed["questions"][0].update(fields)
            with (
                self.subTest(fields=fields),
                self.assertRaises(BankValidationError),
            ):
                validate_bank(changed)
        for fields in (
            {"metadata": []},
            {"snapshot_family": " "},
            {"verification_status": "official_current"},
        ):
            changed = copy.deepcopy(data)
            changed["source"].update(fields)
            with (
                self.subTest(source=fields),
                self.assertRaises(BankValidationError),
            ):
                validate_bank(changed)
        changed = copy.deepcopy(data)
        del changed["questions"][0]["source_classification"]
        with self.assertRaises(BankValidationError):
            validate_bank(changed)

    def test_cli_replacement_preview_and_import_share_flags_and_summary(self):
        first = convert_corpus(self.input).bank
        self.import_snapshot(first)
        raw = external_question()
        raw["question"] = "Replacement synthetic content?"
        replace_domain(self.input, 1, [raw])
        path = self.root / "next.json"
        path.write_text(bank_json(convert_corpus(self.input).bank))
        args = ["--db", str(self.root / "study.db"), "import-json", str(path)]
        with redirect_stdout(StringIO()) as out:
            with self.assertRaises(SystemExit) as error:
                main([*args, "--dry-run"])
        self.assertEqual(error.exception.code, 2)
        self.assertIn(
            "supersede-source", json.loads(out.getvalue())["conflicts"][0]
        )
        before = list(self.conn.iterdump())
        args += ["--supersede-source", first.source.key]
        with redirect_stdout(StringIO()) as preview:
            main([*args, "--dry-run"])
        self.assertEqual(list(self.conn.iterdump()), before)
        with (
            redirect_stdout(StringIO()) as actual,
            redirect_stderr(StringIO()) as err,
        ):
            main(args)
        self.assertEqual(
            json.loads(actual.getvalue()), json.loads(preview.getvalue())
        )
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(
            json.loads(actual.getvalue())["superseded_sources"],
            [first.source.key],
        )
