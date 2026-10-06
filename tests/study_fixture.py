"""Synthetic bank fixtures shared by persistence regression tests."""
import json
import tempfile
import unittest
from pathlib import Path

from aws_study.importers import import_internal_bank
from aws_study.db import connect, init_db


def question(index=1, source="pretest"):
    """Synthetic normalized assessment record; no private bank content."""
    return {
        "source": source, "source_index": index,
        "question": "Synthetic Amazon S3 or Amazon EC2 question?",
        "status": "Incorrect", "original_confidence": "Educated guess",
        "time_to_answer_seconds": 1.5, "dedup_role": "canonical",
        "options": [
            {"letter": "A", "label": "Amazon EC2", "correct": False,
             "selected": True, "rationale": "Synthetic wrong rationale"},
            {"letter": "B", "label": "Amazon S3", "correct": True,
             "selected": False, "rationale": "Synthetic right rationale"},
        ],
    }


class BankTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "study.db")
        self.addCleanup(self.conn.close)
        init_db(self.conn)

    def import_questions(self, questions, **settings):
        path = self.root / "bank.json"
        path.write_text(json.dumps({
            "schema_version": 7, "questions": questions,
        }), encoding="utf-8")
        defaults = {
            "cert_code": "TEST-C01", "cert_name": "Synthetic certification",
            "observed_year": 2026, "verified_year": 2026,
        }
        defaults.update(settings)
        return import_internal_bank(self.conn, path, **defaults)
