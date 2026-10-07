"""Synthetic bank fixtures; private content/history never enters tests."""

import json
import tempfile
import unittest
from pathlib import Path

from aws_study.db import connect, init_db
from aws_study.importers import import_internal_bank


def question(index=1, source="pretest"):
    return {
        "source_ref": str(index),
        "question": f"Synthetic question {index}?",
        "type": "single_select",
        "select_count": 1,
        "classification": {"area": "Compute", "topic": "Synthetic topic"},
        "answers": [
            {
                "text": "Amazon EC2",
                "correct": False,
                "rationale": "Wrong rationale",
            },
            {
                "text": "Amazon S3",
                "correct": True,
                "rationale": "Right rationale",
            },
        ],
    }


def bank(questions, *, source="pretest", cert="TEST-C01", year=2026):
    return {
        "schema_version": 2,
        "certification": {
            "provider": "AWS",
            "code": cert,
            "name": "Synthetic certification",
        },
        "source": {
            "key": source,
            "name": "Official Pretest" if source == "pretest" else source,
            "type": "official",
            "observed_year": year,
            "verified_year": year,
            "verification_status": "official_current",
        },
        "classification": {
            "origin": "curated_from_source_content",
            "fields": ["area", "topic"],
        },
        "questions": questions,
    }


def converted_fixture(old_bank, cert="CLF-C02"):
    """Adapt older synthetic test descriptions to the new input shape only."""
    records = old_bank["questions"] if isinstance(old_bank, dict) else old_bank
    return bank(
        [
            {
                "source_ref": str(q.get("source_index", index)),
                "question": q["question"],
                "type": q.get("question_type", "single_select"),
                "select_count": sum(
                    bool(o.get("correct")) for o in q["options"]
                ),
                "classification": {
                    "area": "Compute",
                    "topic": "Synthetic topic",
                },
                "answers": [
                    {
                        "text": o["label"],
                        "correct": bool(o.get("correct")),
                        "rationale": o.get("rationale"),
                    }
                    for o in q["options"]
                ],
            }
            for index, q in enumerate(records, 1)
        ],
        source="synthetic",
        cert=cert,
    )


class BankTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "study.db")
        self.addCleanup(self.conn.close)
        init_db(self.conn)

    def import_questions(
        self, questions, *, source="pretest", cert_code="TEST-C01", **settings
    ):
        data = bank(questions, source=source, cert=cert_code)
        for field in ("observed_year", "verified_year", "verification_status"):
            if field in settings:
                data["source"][field] = settings[field]
        if "cert_name" in settings:
            data["certification"]["name"] = settings["cert_name"]
        path = self.root / "bank.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return import_internal_bank(self.conn, path)


def remove_draft_schema(conn):
    """Remove v6-only tables when constructing an older synthetic database."""
    for table in (
        "session_response_answers",
        "session_responses",
        "archived_attempt_options",
        "archived_attempts",
    ):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
