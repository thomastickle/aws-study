"""Draft migration archives legacy unfinished history without discarding records."""

import sqlite3
from contextlib import closing

from study_fixture import BankTestCase, question, remove_draft_schema
from test_migrations import legacy_fixture
from test_schema_v5 import remove_v5_invariants

from aws_study.db import (
    SCHEMA_VERSION,
    connect,
    init_db,
    open_readonly,
    schema_version,
)
from aws_study.migration_repository import MigrationRepository
from aws_study.migrations import migrate_db
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


class SchemaV6Tests(BankTestCase):
    def setUp(self):
        super().setUp()
        multi = question(2)
        multi.update(type="multi_select", select_count=2)
        multi["answers"] += [{"text": "Other correct", "correct": True}]
        self.import_questions([question(1), multi])
        self.repo = QuizRepository(self.conn)
        self.service = QuizService(self.repo)
        self.sessions = []
        for mode, kind, completed in (
            ("exam", "interactive", False),
            ("exam", "interactive", True),
            ("exam", "imported_baseline", False),
            ("study", "interactive", False),
        ):
            sid = self.service.create_session(
                1,
                count=2,
                target_year=2026,
                mode=mode,
                strategy="random",
                seed=1,
            )
            self.sessions.append(sid)
            # Reconstruct the previous application's immediate-attempt behavior.
            with self.repo.transaction():
                self.conn.execute(
                    "UPDATE sessions SET source_kind=? WHERE id=?", (kind, sid)
                )
                q = next(
                    q
                    for q in self.service.questions(sid)
                    if q.select_count == 2
                )
                self.repo.insert_attempt(
                    sid,
                    q.id,
                    {q.options[0].id},
                    attempted_at="2026-01-01",
                    is_correct=False,
                    confidence="low",
                    elapsed_ms=None,
                )
                self.conn.execute(
                    "UPDATE attempts SET note='Original note' WHERE session_id=?",
                    (sid,),
                )
                if completed:
                    self.repo.complete_session(sid, "2026-01-02")
        self.seed_path = self.root / "seed.db"
        with closing(sqlite3.connect(self.seed_path)) as seed:
            self.conn.backup(seed)

    @staticmethod
    def rows(conn, table):
        return [
            tuple(r)
            for r in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")
        ]

    def downgrade(self, conn, version):
        remove_draft_schema(conn)
        if version < 5:
            remove_v5_invariants(conn)
        if version < 4:
            conn.execute("DROP TABLE session_answers")
            conn.execute("DROP INDEX idx_answer_question_identity")
        if version < 3:
            conn.execute("DROP TABLE question_selection_groups")
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()

    def test_every_canonical_upgrade_archives_only_unfinished_interactive_exams(
        self,
    ):
        for version in (2, 3, 4, 5):
            with (
                self.subTest(version=version),
                closing(connect(self.root / f"v{version}.db")) as conn,
            ):
                with closing(sqlite3.connect(self.seed_path)) as seed:
                    seed.backup(conn)
                self.downgrade(conn, version)
                original = self.rows(conn, "attempts")
                selected = self.rows(conn, "attempt_options")
                sessions = self.rows(conn, "sessions")
                expected = MigrationRepository(conn).history_counts()
                init_db(conn)
                self.assertEqual(schema_version(conn), 6)
                self.assertEqual(
                    self.rows(conn, "archived_attempts"), original[:1]
                )
                self.assertEqual(
                    self.rows(conn, "archived_attempt_options"), selected[:1]
                )
                self.assertEqual(self.rows(conn, "attempts"), original[1:])
                self.assertEqual(
                    self.rows(conn, "attempt_options"), selected[1:]
                )
                self.assertEqual(self.rows(conn, "sessions"), sessions)
                MigrationRepository(conn).validate(expected)
                sid = self.sessions[0]
                service = QuizService(QuizRepository(conn))
                items = service.resume_session(sid)
                response = next(i for i in items if i.response.selected)
                self.assertEqual(response.status, "INCOMPLETE")
                self.assertEqual(response.response.confidence, "low")
                self.assertIsNone(response.response.elapsed_ms)
                self.assertEqual(
                    response.response.first_answered_at, "2026-01-01"
                )
                self.assertEqual(
                    conn.execute("PRAGMA foreign_key_check").fetchall(), []
                )
                before = self.rows(conn, "archived_attempts")
                dump = list(conn.iterdump())
                init_db(conn)
                self.assertEqual(list(conn.iterdump()), dump)
                for item in items:
                    service.save_response(
                        sid,
                        item.question.id,
                        {o.id for o in item.question.options if o.correct},
                        100,
                    )
                service.submit_session(sid)
                self.assertEqual(self.rows(conn, "archived_attempts"), before)
                new_ids = {
                    r[0]
                    for r in conn.execute(
                        "SELECT id FROM attempts WHERE session_id=?", (sid,)
                    )
                }
                self.assertNotIn(original[0][0], new_ids)

    def test_failed_conversion_rolls_back_schema_and_original_history(self):
        self.downgrade(self.conn, 5)
        # Previous schema allowed an attempt's selected answer from another question.
        attempt = self.conn.execute(
            "SELECT id FROM attempts WHERE session_id=1"
        ).fetchone()[0]
        other = self.conn.execute(
            "SELECT id FROM answers WHERE question_id=1 LIMIT 1"
        ).fetchone()[0]
        self.conn.execute(
            "UPDATE attempt_options SET option_id=? WHERE attempt_id=?",
            (other, attempt),
        )
        self.conn.commit()
        before = list(self.conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            init_db(self.conn)
        self.assertEqual(list(self.conn.iterdump()), before)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(schema_version(self.conn), 5)

    def test_v1_conversion_also_archives_original_unfinished_interactive_attempts(
        self,
    ):
        path = self.root / "legacy.db"
        with closing(legacy_fixture(path)) as old:
            old.execute(
                "UPDATE sessions SET completed_at=NULL WHERE source_kind='interactive'"
            )
            old.commit()
            expected = old.execute("""SELECT COUNT(*) FROM attempts a JOIN sessions s ON s.id=a.session_id
                WHERE s.source_kind='interactive'""").fetchone()[0]
        original = path.read_bytes()
        dest = self.root / "legacy-upgraded.db"
        summary = migrate_db(path, dest)
        self.assertEqual(summary["attempts_preserved"], 3)
        self.assertEqual(path.read_bytes(), original)
        with closing(open_readonly(dest)) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM archived_attempts"
                ).fetchone()[0],
                expected,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                3 - expected,
            )
            self.assertEqual(
                conn.execute("PRAGMA foreign_key_check").fetchall(), []
            )

    def test_current_database_copy_preserves_drafts_flags_and_archives(self):
        self.downgrade(self.conn, 5)
        init_db(self.conn)
        sid = self.sessions[0]
        self.service.toggle_flag(sid, self.service.questions(sid)[0].id)
        expected = self.service.review_items(sid)
        archived = self.rows(self.conn, "archived_attempts")
        dest = self.root / "copy.db"
        summary = migrate_db(self.root / "study.db", dest)
        self.assertEqual(summary["source_schema_version"], SCHEMA_VERSION)
        self.assertEqual(
            summary["session_responses_preserved"],
            len(self.repo.responses(sid)),
        )
        with closing(connect(dest)) as conn:
            self.assertEqual(
                QuizService(QuizRepository(conn)).resume_session(sid), expected
            )
            self.assertEqual(self.rows(conn, "archived_attempts"), archived)

    def test_draft_foreign_keys_and_constraints_reject_wrong_session_answers(
        self,
    ):
        sid = self.sessions[0]
        q = self.service.questions(sid)[0]
        self.service.toggle_flag(sid, q.id)
        for sql, params in (
            (
                "INSERT INTO session_responses(session_id,question_id) VALUES (?,999)",
                (sid,),
            ),
            (
                "INSERT INTO session_response_answers VALUES (?,?,999)",
                (sid, q.id),
            ),
            ("UPDATE session_responses SET confidence='bad'", ()),
            ("UPDATE session_responses SET elapsed_ms=-1", ()),
            ("UPDATE session_responses SET flagged=2", ()),
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute(sql, params)
            self.conn.rollback()
        self.conn.execute("DROP TABLE session_response_answers")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "Incomplete v6"):
            init_db(self.conn)

    def test_current_schema_requires_draft_membership_foreign_keys(self):
        self.conn.executescript("""
            DROP TABLE session_response_answers;
            CREATE TABLE session_response_answers (
                session_id INTEGER, question_id INTEGER, answer_id INTEGER,
                PRIMARY KEY(session_id,question_id,answer_id)
            );
        """)
        with self.assertRaisesRegex(ValueError, "missing draft foreign keys"):
            init_db(self.conn)
