"""Additive attempt invariants preserve history or reject an invalid upgrade."""

import sqlite3

from study_fixture import BankTestCase, question

from aws_study.db import SCHEMA_VERSION, init_db, schema_version
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService


def remove_v5_invariants(conn):
    for sql in (
        "DROP INDEX idx_attempt_session_question",
        "DROP INDEX idx_attempt_question_recent",
        "DROP TRIGGER attempts_nonnegative_elapsed_insert",
        "DROP TRIGGER attempts_nonnegative_elapsed_update",
    ):
        conn.execute(sql)


class SchemaV5Tests(BankTestCase):
    def setUp(self):
        super().setUp()
        self.import_questions([question()])
        service = QuizService(QuizRepository(self.conn))
        self.sid = service.create_session(
            1,
            count=1,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=2,
        )
        item = service.questions(self.sid)[0]
        service.record_answer(
            self.sid,
            item.id,
            {o.id for o in item.options if o.correct},
            None,
            42,
        )
        service.finish_session(self.sid)

    def old_database(self, version):
        remove_v5_invariants(self.conn)
        if version < 4:
            self.conn.execute("DROP TABLE session_answers")
            self.conn.execute("DROP INDEX idx_answer_question_identity")
        if version < 3:
            self.conn.execute("DROP TABLE question_selection_groups")
        self.conn.execute(f"PRAGMA user_version={version}")
        self.conn.commit()

    def test_supported_version_upgrades_preserve_rows_and_are_idempotent(self):
        for version in (2, 3, 4):
            with self.subTest(version=version):
                self.old_database(version)
                tables = [
                    r[0]
                    for r in self.conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                ]
                before = {
                    table: [
                        tuple(row)
                        for row in self.conn.execute(
                            f"SELECT * FROM {table} ORDER BY rowid"
                        )
                    ]
                    for table in tables
                }
                init_db(self.conn)
                self.assertEqual(schema_version(self.conn), SCHEMA_VERSION)
                for table, rows in before.items():
                    self.assertEqual(
                        [
                            tuple(row)
                            for row in self.conn.execute(
                                f"SELECT * FROM {table} ORDER BY rowid"
                            )
                        ],
                        rows,
                    )
                self.assertEqual(
                    self.conn.execute("PRAGMA foreign_key_check").fetchall(),
                    [],
                )
                dump = list(self.conn.iterdump())
                init_db(self.conn)
                self.assertEqual(list(self.conn.iterdump()), dump)

    def test_attempt_uniqueness_and_elapsed_insert_update_constraints(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO attempts(session_id,question_id,is_correct) VALUES (?,1,0)",
                (self.sid,),
            )
        self.conn.rollback()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "negative"):
            self.conn.execute("UPDATE attempts SET elapsed_ms=-1")
        self.conn.rollback()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "negative"):
            self.conn.execute(
                "INSERT INTO attempts(session_id,question_id,is_correct,elapsed_ms) VALUES (?,1,0,-1)",
                (self.sid,),
            )
        self.conn.rollback()
        for value in (None, 0, 42):
            self.conn.execute("UPDATE attempts SET elapsed_ms=?", (value,))
            self.conn.commit()

    def test_incompatible_history_aborts_entire_upgrade(self):
        for problem in ("negative", "duplicate"):
            with self.subTest(problem=problem):
                self.old_database(2)
                if problem == "negative":
                    self.conn.execute("UPDATE attempts SET elapsed_ms=-1")
                else:
                    self.conn.execute(
                        "INSERT INTO attempts(session_id,question_id,is_correct) VALUES (?,1,0)",
                        (self.sid,),
                    )
                self.conn.commit()
                before = list(self.conn.iterdump())
                with self.assertRaises(sqlite3.IntegrityError):
                    init_db(self.conn)
                self.assertEqual(list(self.conn.iterdump()), before)
                self.assertFalse(self.conn.in_transaction)
                self.assertEqual(
                    self.conn.execute(
                        "SELECT name FROM sqlite_temp_master"
                    ).fetchall(),
                    [],
                )
                # Restore only the synthetic invalid row so the next case can run.
                self.conn.execute("DELETE FROM attempts WHERE id>1")
                self.conn.execute("UPDATE attempts SET elapsed_ms=42")
                self.conn.commit()
                init_db(self.conn)

    def test_missing_v5_invariant_is_not_accepted_as_current_schema(self):
        self.conn.execute("DROP TRIGGER attempts_nonnegative_elapsed_update")
        self.conn.commit()
        with self.assertRaisesRegex(ValueError, "Incomplete v5"):
            init_db(self.conn)
