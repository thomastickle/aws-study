"""Additive provenance migration preserves every pre-existing history field."""

import sqlite3
from importlib.resources import files
from unittest.mock import patch

from study_fixture import BankTestCase, question, remove_source_schema

from aws_study.db import init_db, schema_version
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


class SchemaV7Tests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.import_questions([question()])
        service = QuizService(QuizRepository(self.conn))
        sid = service.create_session(
            1,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        q = service.questions(sid)[0]
        service.save_response(
            sid, q.id, {o.id for o in q.options if o.correct}, 20
        )
        service.toggle_flag(sid, q.id)
        self.conn.execute(
            "INSERT INTO review_notes(question_id,certification_id,note_text) VALUES (1,1,'Synthetic note')"
        )
        self.conn.commit()
        remove_source_schema(self.conn)
        self.conn.execute("PRAGMA user_version=6")
        self.conn.commit()

    def test_v6_upgrade_backfills_taxonomy_and_preserves_original_fields(self):
        tables = [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        before = {
            table: [
                dict(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in tables
        }
        init_db(self.conn)
        self.assertEqual(schema_version(self.conn), 7)
        for table, expected in before.items():
            after = [
                dict(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            self.assertEqual(
                [
                    {key: row[key] for key in original}
                    for original, row in zip(expected, after)
                ],
                expected,
            )
            self.assertEqual(len(after), len(expected))
        source = self.conn.execute(
            "SELECT source_area,source_topic,explanation,verified_at FROM question_sources"
        ).fetchone()
        self.assertEqual(
            tuple(source), ("Compute", "Synthetic topic", None, None)
        )
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(), []
        )
        dump = list(self.conn.iterdump())
        init_db(self.conn)
        self.assertEqual(list(self.conn.iterdump()), dump)

    def test_failed_v7_upgrade_rolls_back_all_additions(self):
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "schema_v7.sql").write_text(
            files("aws_study").joinpath("schema_v7.sql").read_text()
            + "\nSELECT * FROM missing;"
        )
        before = list(self.conn.iterdump())
        with patch("aws_study.db.files", return_value=assets):
            with self.assertRaises(sqlite3.OperationalError):
                init_db(self.conn)
        self.assertEqual(schema_version(self.conn), 6)
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertFalse(self.conn.in_transaction)

    def test_incomplete_v7_is_rejected(self):
        init_db(self.conn)
        self.conn.execute(
            "ALTER TABLE question_sources RENAME COLUMN explanation TO missing"
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "Incomplete v7"):
            init_db(self.conn)

    def test_supersession_foreign_key_is_required(self):
        init_db(self.conn)
        self.conn.execute(
            "ALTER TABLE sources DROP COLUMN superseded_by_source_id"
        )
        self.conn.execute(
            "ALTER TABLE sources ADD COLUMN superseded_by_source_id INTEGER"
        )
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "supersession foreign key"):
            init_db(self.conn)
