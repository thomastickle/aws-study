import json
import tempfile
import unittest
from pathlib import Path

from aws_study.db import connect, init_db
from aws_study.importers import import_internal_bank
from aws_study.selection import candidates


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.conn = connect(self.db)
        init_db(self.conn)
        bank = {
            "schema_version": 1,
            "questions": [{
                "source":"sample", "source_index":1, "question":"Synthetic AWS test?",
                "status":"Incorrect", "question_type":"single_select",
                "options":[
                    {"letter":"A","label":"Wrong","correct":False,"selected":True,"rationale":"no"},
                    {"letter":"B","label":"Right","correct":True,"selected":False,"rationale":"yes"}
                ], "dedup_role":"canonical"
            }]
        }
        self.bank_path = Path(self.tmp.name) / "bank.json"
        self.bank_path.write_text(json.dumps(bank))
        import_internal_bank(self.conn, self.bank_path, cert_code="TEST-C01", observed_year=2026, verified_year=2026)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_import(self):
        self.assertEqual(self.conn.execute("select count(*) from questions").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("select count(*) from attempts").fetchone()[0], 1)

    def test_adaptive_candidate_weights_miss(self):
        cid = self.conn.execute("select id from certifications where code='TEST-C01'").fetchone()[0]
        c = candidates(self.conn, cid, target_year=2026, strategy="adaptive")
        self.assertEqual(len(c), 1)
        self.assertGreater(c[0].weight, 5.0)

    def test_year_filter(self):
        cid = self.conn.execute("select id from certifications where code='TEST-C01'").fetchone()[0]
        c = candidates(self.conn, cid, target_year=2025, strategy="random")
        self.assertEqual(c, [])


if __name__ == "__main__":
    unittest.main()
