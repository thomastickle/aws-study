"""Public schema and immutable import models agree with supported JSON banks."""

import copy
import importlib.util
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from study_fixture import BankTestCase

from aws_study.bank_schema import load_bank


class ValidatedModelTests(BankTestCase):
    def test_public_sample_loads_typed_immutable_metadata(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "examples/question-bank.sample.json"
        )
        bank = load_bank(path)
        self.assertEqual(bank.certification.provider, "AWS")
        self.assertEqual(bank.certification.code, "EXAMPLE-C01")
        self.assertEqual(bank.source.key, "synthetic-example-2026")
        self.assertEqual(bank.source.kind, "synthetic")
        self.assertEqual(bank.source.observed_year, 2026)
        self.assertEqual(bank.source.verification_status, "verified_current")
        for metadata, name in (
            (bank.certification, "code"),
            (bank.source, "key"),
        ):
            with self.assertRaises(FrozenInstanceError):
                setattr(metadata, name, "changed")

    def test_optional_years_and_extra_metadata_preserve_input_compatibility(
        self,
    ):
        path = (
            Path(__file__).resolve().parents[1]
            / "examples/question-bank.sample.json"
        )
        data = json.loads(path.read_text())
        del data["source"]["observed_year"]
        data["source"]["verified_year"] = None
        data["source"]["custom"] = {"note": "ignored metadata"}
        data["certification"]["version"] = "ignored metadata"
        test_path = self.root / "bank.json"
        test_path.write_text(json.dumps(data))
        bank = load_bank(test_path)
        self.assertIsNone(bank.source.observed_year)
        self.assertIsNone(bank.source.verified_year)


@unittest.skipUnless(
    importlib.util.find_spec("jsonschema"),
    "Schema validation uses development-only jsonschema",
)
class JsonSchemaTests(unittest.TestCase):
    def setUp(self):
        from jsonschema import Draft202012Validator

        examples = Path(__file__).resolve().parents[1] / "examples"
        schema = json.loads(
            (examples / "question-bank.schema.json").read_text()
        )
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)
        self.sample = json.loads(
            (examples / "question-bank.sample.json").read_text()
        )

    def test_sample_is_current_schema(self):
        self.validator.validate(self.sample)

    def test_invalid_current_format_and_runtime_fields_are_rejected(self):
        cases = []
        for version in (None, 1, 3, True):
            data = copy.deepcopy(self.sample)
            data["schema_version"] = version
            cases.append(data)
        for missing in (
            "schema_version",
            "source",
            "certification",
            "questions",
        ):
            data = copy.deepcopy(self.sample)
            del data[missing]
            cases.append(data)
        for field, value in (
            ("question", "  "),
            ("select_count", 2),
            ("status", "answered"),
            ("selection_group", ""),
            ("classification", {"area": "Compute"}),
        ):
            data = copy.deepcopy(self.sample)
            data["questions"][0][field] = value
            cases.append(data)
        for value in (True, 0, 10000, "2026"):
            data = copy.deepcopy(self.sample)
            data["source"]["observed_year"] = value
            cases.append(data)
        data = copy.deepcopy(self.sample)
        data["questions"][0]["answers"][0]["selected"] = True
        cases.append(data)
        for index, data in enumerate(cases):
            with self.subTest(case=index):
                self.assertFalse(self.validator.is_valid(data))

    def test_optional_fields_extra_metadata_and_multiselect(self):
        data = copy.deepcopy(self.sample)
        del data["source"]["observed_year"]
        data["source"]["verified_year"] = None
        data["certification"]["version"] = "ignored"
        item = data["questions"][0]
        item["type"] = "multi_select"
        item["select_count"] = 2
        item["selection_group"] = "synthetic-group"
        item["source_ref"] = None
        item["answers"][0]["correct"] = True
        del item["answers"][0]["rationale"]
        item["answers"][1]["rationale"] = None
        item["custom_metadata"] = ["ignored"]
        self.validator.validate(data)
