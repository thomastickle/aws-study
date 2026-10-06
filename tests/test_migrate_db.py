"""Version-neutral migration supports every database version without source writes."""

import json
import sqlite3
from contextlib import closing, redirect_stderr, redirect_stdout
from importlib.resources import files
from io import StringIO
from unittest.mock import patch

from study_fixture import BankTestCase, question
from test_migrations import legacy_fixture
from test_schema_v5 import remove_v5_invariants

from aws_study.cli import main
from aws_study.db import (
    MIGRATION_SCRIPTS,
    SCHEMA_VERSION,
    init_db,
    open_readonly,
    schema_version,
)
from aws_study.migrations import migrate_db
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


class DatabaseMigrationTests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.root / "study.db"
        self.import_questions([question(1), question(2)])
        service = QuizService(QuizRepository(self.conn))
        sid = service.create_session(
            1,
            count=2,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        for item in service.questions(sid):
            service.record_answer(
                sid,
                item.id,
                {o.id for o in item.options if o.correct},
                "high",
                1234,
            )
        service.finish_session(sid)

    def downgrade(self, version):
        remove_v5_invariants(self.conn)
        if version < 4:
            self.conn.execute("DROP TABLE session_answers")
            self.conn.execute("DROP INDEX idx_answer_question_identity")
        if version < 3:
            self.conn.execute("DROP TABLE question_selection_groups")
        self.conn.execute(f"PRAGMA user_version={version}")
        self.conn.commit()

    def snapshot(self, conn):
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        return {
            table: [
                tuple(r)
                for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
            for table in tables
        }

    def test_canonical_versions_migrate_with_wal_history_and_saved_answer_order(
        self,
    ):
        for version in (2, 3, 4, 5):
            with self.subTest(version=version):
                if version != 5:
                    self.downgrade(version)
                before = self.snapshot(self.conn)
                source_bytes = self.source.read_bytes()
                dest = self.root / f"from-{version}.db"
                summary = migrate_db(self.source, dest)
                self.assertEqual(summary["source_schema_version"], version)
                self.assertEqual(
                    summary["target_schema_version"], SCHEMA_VERSION
                )
                self.assertEqual(summary["attempts_preserved"], 2)
                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(self.snapshot(self.conn), before)
                self.assertEqual(schema_version(self.conn), version)
                with closing(open_readonly(dest)) as upgraded:
                    after = self.snapshot(upgraded)
                    for table, rows in before.items():
                        self.assertEqual(after[table], rows)
                    self.assertEqual(schema_version(upgraded), SCHEMA_VERSION)
                    self.assertEqual(
                        upgraded.execute(
                            "PRAGMA foreign_key_check"
                        ).fetchall(),
                        [],
                    )
                self.assertEqual(
                    list(self.root.glob(".aws-study-migration-*")), []
                )
                init_db(self.conn)

    def test_cli_migrates_v1_and_reports_actual_versions(self):
        path = self.root / "legacy.db"
        with closing(legacy_fixture(path)):
            pass
        dest = self.root / "cli-current.db"
        before = path.read_bytes()
        with redirect_stdout(StringIO()) as output:
            main(["migrate-db", "--source", str(path), "--dest", str(dest)])
        summary = json.loads(output.getvalue())
        self.assertEqual(summary["source_schema_version"], 1)
        self.assertEqual(summary["target_schema_version"], SCHEMA_VERSION)
        self.assertEqual(summary["attempts_preserved"], 3)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_versioned_upgrade_publishes_nothing_and_keeps_source(self):
        self.downgrade(4)
        self.conn.execute("UPDATE attempts SET elapsed_ms=-1")
        self.conn.commit()
        before = self.snapshot(self.conn)
        dest = self.root / "failed.db"
        with self.assertRaisesRegex(sqlite3.IntegrityError, "nonnegative"):
            migrate_db(self.source, dest)
        self.assertFalse(dest.exists())
        self.assertEqual(schema_version(self.conn), 4)
        self.assertEqual(self.snapshot(self.conn), before)
        self.assertEqual(list(self.root.glob(".aws-study-migration-*")), [])

    def test_invalid_history_in_current_database_fails_validation(self):
        self.conn.execute("UPDATE attempts SET is_correct=0")
        self.conn.commit()
        dest = self.root / "invalid.db"
        with self.assertRaisesRegex(ValueError, "correctness mismatch"):
            migrate_db(self.source, dest)
        self.assertFalse(dest.exists())
        self.assertEqual(list(self.root.glob(".aws-study-migration-*")), [])

    def test_missing_empty_and_newer_sources_are_rejected(self):
        missing = self.root / "missing.db"
        with self.assertRaises(sqlite3.OperationalError):
            migrate_db(missing, self.root / "missing-output.db")
        self.assertFalse(missing.exists())
        empty = self.root / "empty.db"
        empty.touch()
        with self.assertRaisesRegex(ValueError, "existing study database"):
            migrate_db(empty, self.root / "empty-output.db")
        self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "Unsupported database schema"):
            migrate_db(self.source, self.root / "newer-output.db")
        self.assertEqual(list(self.root.glob(".aws-study-migration-*")), [])

    def test_publication_race_cannot_overwrite_an_existing_destination(self):
        dest = self.root / "raced.db"

        def concurrent_destination(temporary, target):
            dest.write_bytes(b"Keep this file")
            raise FileExistsError("Destination created concurrently")

        with patch(
            "aws_study.migrations.os.link", side_effect=concurrent_destination
        ):
            with self.assertRaises(FileExistsError):
                migrate_db(self.source, dest)
        self.assertEqual(dest.read_bytes(), b"Keep this file")
        self.assertEqual(list(self.root.glob(".aws-study-migration-*")), [])

    def test_migration_steps_cover_every_canonical_version(self):
        self.assertEqual(
            set(MIGRATION_SCRIPTS), set(range(2, SCHEMA_VERSION + 1))
        )
        for target, filename in MIGRATION_SCRIPTS.items():
            self.assertIn(
                f"PRAGMA user_version = {target};",
                files("aws_study").joinpath(filename).read_text(),
            )

    def test_removed_version_specific_command_is_no_longer_advertised(self):
        with (
            redirect_stdout(StringIO()) as output,
            self.assertRaises(SystemExit) as exit_status,
        ):
            main(["--help"])
        self.assertEqual(exit_status.exception.code, 0)
        self.assertIn("migrate-db", output.getvalue())
        with (
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as exit_status,
        ):
            main(["migrate-v1-to-v2"])
        self.assertEqual(exit_status.exception.code, 2)
