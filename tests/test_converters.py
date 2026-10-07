"""Source conversion, validation, audit, and publication stay offline."""

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from converter_fixture import external_question, replace_domain, write_corpus

from aws_study.bank_schema import BankValidationError, load_bank, validate_bank
from aws_study.bank_serialization import bank_json, bank_mapping
from aws_study.cli import main
from aws_study.converters import write_bank
from aws_study.converters.cloudcertprep import (
    FAMILY,
    audit_corpus,
    convert_corpus,
    convert_record,
)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input"
        write_corpus(self.input)

    def test_single_multi_and_option_order(self):
        for multi, five in ((False, False), (True, False), (True, True)):
            with self.subTest(multi=multi, five=five):
                raw = external_question(multi=multi, five=five)
                raw["options"] = dict(reversed(list(raw["options"].items())))
                question = convert_record(raw, 1)
                self.assertEqual(question.select_count, 2 if multi else 1)
                self.assertEqual(
                    [a.text for a in question.answers],
                    list(raw["options"].values()),
                )
                self.assertEqual(
                    [a.text for a in question.answers if a.correct],
                    [
                        text
                        for key, text in raw["options"].items()
                        if key in raw["answer"]
                    ],
                )
                self.assertEqual(question.explanation, raw["explanation"])
                self.assertTrue(
                    all(a.rationale is None for a in question.answers)
                )

    def test_metadata_and_explicit_types_are_lossless(self):
        raw = external_question()
        raw.update(
            type="single",
            domainId=1,
            lastVerified="2026-06-15",
            taskStatement="1.2",
            services=["Amazon EC2"],
            upstreamNote={"value": [1, True]},
        )
        q = convert_record(raw, 1)
        self.assertEqual(q.verified_at, "2026-06-15")
        self.assertEqual(q.verification_status, "verified_current")
        assert q.source_classification is not None
        self.assertEqual(q.source_classification.topic, "1.2")
        self.assertEqual((q.source_metadata or {})["services"], ["Amazon EC2"])
        self.assertEqual(
            (q.source_metadata or {})["upstream_fields"],
            {"upstreamNote": {"value": [1, True]}},
        )
        plain = convert_record(external_question(), 1)
        self.assertIsNone(plain.verified_at)
        self.assertEqual(plain.verification_status, "unverified")
        assert plain.source_classification is not None
        self.assertIsNone(plain.source_classification.topic)
        raw["services"] = []
        self.assertEqual(
            (convert_record(raw, 1).source_metadata or {})["services"], []
        )

    def test_invalid_external_records_are_rejected(self):
        changes = [
            {"id": ""},
            {"question": " "},
            {"options": None},
            {"options": {"A": "", "B": "Other"}},
            {"options": {"A": " Same ", "B": "same"}},
            {"options": {"Z": "Wrong", "B": "Other"}},
            {"answer": "E"},
            {"answer": None},
            {"answer": ["A"]},
            {"isMultiAnswer": "true"},
            {"isMultiAnswer": True},
            {"isMultiAnswer": True, "answer": ["A"]},
            {"isMultiAnswer": True, "answer": ["A", "A"]},
            {"isMultiAnswer": True, "answer": ["A", {}]},
            {"type": "multi"},
            {"type": "ordering"},
            {"targets": {}},
            {"domainId": 2},
            {"domainId": True},
            {"lastVerified": "2026-02-30"},
            {"lastVerified": "20260615"},
            {"lastVerified": None},
            {"taskStatement": None},
            {"taskStatement": "2.1"},
            {"taskStatement": "1.9"},
            {"services": "Amazon EC2"},
            {"services": [""]},
            {"explanation": {}},
            {"elapsed_ms": 10},
        ]
        for fields in changes:
            raw = external_question()
            raw.update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                convert_record(raw, 1)
        for raw, domain in ((None, 1), (external_question(), 99)):
            with self.assertRaises(ValueError):
                convert_record(raw, domain)

    def test_audit_and_conversion_use_same_records_and_preserve_anomaly(self):
        raw = external_question(multi=True, five=True)
        raw.update(
            id="aif-synthetic",
            lastVerified="2026-06-15",
            taskStatement="1.1",
            services=[],
        )
        replace_domain(self.input, 1, [raw])
        result = convert_corpus(self.input)
        self.assertEqual(result.audit, audit_corpus(self.input))
        self.assertEqual(result.audit.total_questions, 4)
        self.assertEqual(result.audit.multi_select, 1)
        self.assertEqual(result.audit.option_counts, {4: 3, 5: 1})
        self.assertEqual(result.audit.without_last_verified, 3)
        self.assertEqual(result.audit.services_present, 1)
        self.assertEqual(result.audit.services_nonempty, 0)
        self.assertIn("aif-synthetic", result.audit.warnings[0])
        self.assertEqual(result.bank.questions[0].source_ref, "aif-synthetic")
        self.assertEqual(result.bank.source.snapshot_family, FAMILY)
        self.assertIsNone(
            (result.bank.source.metadata or {})["upstream_revision"]
        )
        self.assertIn(
            "Copyright (c) 2026 Alex Santonastaso",
            str((result.bank.source.metadata or {})["license_notice"]),
        )
        self.assertEqual(
            bank_json(result.bank), bank_json(convert_corpus(self.input).bank)
        )
        self.assertEqual(validate_bank(bank_mapping(result.bank)), result.bank)

    def test_audit_duplicates_variants_and_collected_errors(self):
        raw = external_question()
        same = copy.deepcopy(raw)
        same["id"] = "q010"
        variant = copy.deepcopy(raw)
        variant["options"]["D"] = "Distinct synthetic distractor"
        replace_domain(self.input, 1, [raw, same, variant])
        replace_domain(self.input, 2, [{"id": "broken"}])
        audit = audit_corpus(self.input)
        self.assertEqual(audit.duplicate_ids, ("q001",))
        self.assertEqual(len(audit.duplicate_stems), 1)
        self.assertEqual(len(audit.duplicate_contents), 1)
        self.assertEqual(len(audit.same_stem_variants), 1)
        with self.assertRaises(BankValidationError) as error:
            convert_corpus(self.input)
        self.assertEqual(len(error.exception.errors), 2)
        self.assertIn("domain1.json", error.exception.errors[0])
        self.assertIn("broken", error.exception.errors[1])

    def test_missing_empty_invalid_and_duplicate_json_files(self):
        (self.input / "domain1.json").unlink()
        (self.input / "domain2.json").write_text('{"id":1,"id":2}')
        (self.input / "domain3.json").write_text("[]")
        (self.input / "domain4.json").write_text("invalid")
        with self.assertRaises(BankValidationError) as error:
            convert_corpus(self.input)
        self.assertEqual(len(error.exception.errors), 4)
        self.assertEqual(audit_corpus(self.input).minimum_options, 0)
        with self.assertRaisesRegex(ValueError, "CLF-C02 only"):
            convert_corpus(self.input, cert="AIF-C01")

    def test_full_copy_ledger_and_actual_notice_without_git(self):
        root = self.root / "copy"
        directory = root / "src/data/clf-c02"
        write_corpus(directory)
        (root / "LICENSE").write_text("Synthetic upstream notice")
        ledger = root / "src/data/bank-lastmod.json"
        ledger.write_text(
            json.dumps(
                {
                    "certs": {
                        "clf-c02": {
                            "contentHash": "a" * 64,
                            "lastmod": "2026-07-05",
                        }
                    }
                }
            )
        )
        result = convert_corpus(root)
        self.assertEqual(
            str((result.bank.source.metadata or {})["license_notice"]),
            "Synthetic upstream notice",
        )
        self.assertEqual(result.bank.source.verification_status, "unverified")
        self.assertIsNone(result.bank.source.verified_year)
        for value in (
            {},
            {"certs": {"clf-c02": {"lastmod": "bad"}}},
            {
                "certs": {
                    "clf-c02": {"lastmod": "2026-07-05", "contentHash": "bad"}
                }
            },
        ):
            ledger.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "freshness ledger"):
                convert_corpus(root)

    def commit_checkout(self, root):
        for args in (
            ("init", "-q"),
            ("config", "core.autocrlf", "true"),
            ("add", "."),
            (
                "-c",
                "user.name=Synthetic",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Fixture",
            ),
        ):
            subprocess.run(
                ["git", "-C", str(root), *args],
                check=True,
                capture_output=True,
            )
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    @unittest.skipUnless(shutil.which("git"), "Git checkout revision test")
    def test_checkout_line_endings_keep_revision_and_snapshot_identity(self):
        root = self.root / "checkout"
        directory = root / "src/data/clf-c02"
        write_corpus(directory)
        for path in directory.iterdir():
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
        notice = root / "LICENSE"
        notice.write_bytes(b"Synthetic upstream notice\n")
        ledger = root / "src/data/bank-lastmod.json"
        ledger.write_bytes(
            json.dumps(
                {
                    "certs": {
                        "clf-c02": {
                            "contentHash": "a" * 64,
                            "lastmod": "2026-07-05",
                        }
                    }
                },
                indent=2,
            ).encode()
            + b"\n"
        )
        sha = self.commit_checkout(root)
        before = convert_corpus(root).bank
        loose_before = convert_corpus(directory).bank
        for path in [*directory.iterdir(), notice, ledger]:
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        subprocess.run(
            ["git", "-C", str(root), "diff", "--exit-code", "HEAD"],
            check=True,
            capture_output=True,
        )
        after = convert_corpus(root).bank
        self.assertEqual(after.source.key, f"{FAMILY}@{sha}")
        self.assertEqual(bank_json(before), bank_json(after))
        self.assertEqual(
            bank_json(loose_before), bank_json(convert_corpus(directory).bank)
        )

    @unittest.skipUnless(shutil.which("git"), "Git checkout revision test")
    def test_clean_checkout_sha_and_dirty_content_digest(self):
        root = self.root / "checkout"
        directory = root / "src/data/clf-c02"
        write_corpus(directory)
        sha = self.commit_checkout(root)
        clean = convert_corpus(root).bank
        self.assertEqual(clean.source.key, f"{FAMILY}@{sha}")
        self.assertEqual(
            (clean.source.metadata or {})["upstream_revision"], sha
        )
        # Loose files inside a Git checkout must not adopt its ancestor's SHA.
        self.assertIsNone(
            (convert_corpus(directory).bank.source.metadata or {})[
                "upstream_revision"
            ]
        )
        raw = external_question()
        raw["explanation"] = "Changed source explanation"
        replace_domain(directory, 1, [raw])
        dirty = convert_corpus(root).bank
        self.assertIn("@sha256-", dirty.source.key)
        self.assertIsNone((dirty.source.metadata or {})["upstream_revision"])
        self.assertEqual(
            (dirty.source.metadata or {})["checkout_revision"], sha
        )
        self.assertTrue((dirty.source.metadata or {})["working_tree_modified"])
        # A full-format copy nested inside another repository also uses a digest.
        nested = root / "nested"
        write_corpus(nested / "src/data/clf-c02")
        self.assertIsNone(
            (convert_corpus(nested).bank.source.metadata or {})[
                "checkout_revision"
            ]
        )

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

    def test_cli_offline_publication_no_database_or_input_writes(self):
        output = self.root / "generated/bank.json"
        missing_db = self.root / "absent/study.db"
        original = {p.name: p.read_bytes() for p in self.input.iterdir()}
        args = [
            "--db",
            str(missing_db),
            "convert",
            "cloudcertprep",
            "--input",
            str(self.input),
            "--cert",
            "CLF-C02",
            "--output",
            str(output),
        ]
        out, err = self.run_cli(args)
        self.assertEqual(json.loads(out)["audit"]["total_questions"], 4)
        self.assertEqual(err, "")
        self.assertFalse(missing_db.parent.exists())
        self.assertEqual(len(load_bank(output).questions), 4)
        before = output.read_bytes()
        self.run_cli(args, failed=True)
        self.assertEqual(output.read_bytes(), before)
        self.run_cli([*args, "--force"])
        self.assertEqual(output.read_bytes(), before)
        self.assertEqual(
            {p.name: p.read_bytes() for p in self.input.iterdir()}, original
        )
        self.assertEqual(list(output.parent.glob(".aws-study-bank-*")), [])
        self.run_cli([*args[:-1], str(self.input / "out.json")], failed=True)
        replace_domain(self.input, 1, [{"id": "invalid"}])
        new_output = self.root / "invalid/output.json"
        self.run_cli([*args[:-1], str(new_output)], failed=True)
        self.assertFalse(new_output.parent.exists())

    def test_publication_race_and_failure_keep_existing_data(self):
        bank = convert_corpus(self.input).bank
        output = self.root / "bank.json"

        def concurrent_output(temporary, target):
            output.write_text("Preserve concurrent content")
            raise FileExistsError("Concurrent output")

        with patch(
            "aws_study.converters.os.link", side_effect=concurrent_output
        ):
            with self.assertRaises(FileExistsError):
                write_bank(bank, output)
        self.assertEqual(output.read_text(), "Preserve concurrent content")
        with patch(
            "aws_study.converters.os.replace",
            side_effect=OSError("Failed publication"),
        ):
            with self.assertRaises(OSError):
                write_bank(bank, output, force=True)
        self.assertEqual(output.read_text(), "Preserve concurrent content")
        self.assertEqual(list(self.root.glob(".aws-study-bank-*")), [])

    def test_cli_refuses_dangling_symlink_and_source_symlink_targets(self):
        output = self.root / "linked.json"
        target = self.root / "absent.json"
        try:
            output.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this platform")
        args = [
            "convert",
            "cloudcertprep",
            "--input",
            str(self.input),
            "--cert",
            "CLF-C02",
            "--output",
            str(output),
        ]
        self.run_cli(args, failed=True)
        self.assertFalse(target.exists())
        self.assertTrue(output.is_symlink())
        self.run_cli([*args, "--force"])
        self.assertFalse(output.is_symlink())
        self.assertFalse(target.exists())
        output.unlink()
        source = self.input / "domain1.json"
        before = source.read_bytes()
        output.symlink_to(source)
        self.run_cli([*args, "--force"], failed=True)
        self.assertEqual(source.read_bytes(), before)
