"""SQL execution respects authored statement boundaries and owned transactions."""

import sqlite3
import unittest
from contextlib import closing

from aws_study.db import execute_migration_script


class MigrationExecutionTests(unittest.TestCase):
    def test_literals_comments_and_triggers_stay_in_the_callers_transaction(
        self,
    ):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE events (value TEXT)")
            conn.execute("CREATE TABLE audit (value TEXT)")
            conn.execute("BEGIN")
            execute_migration_script(
                conn,
                """
                -- A comment containing ; is not a statement boundary.
                CREATE TRIGGER log_event AFTER INSERT ON events BEGIN
                    INSERT INTO audit VALUES ('trigger;first');
                    INSERT INTO audit VALUES (NEW.value);
                END;
                INSERT INTO events VALUES ('value;with;semicolons'); INSERT INTO events VALUES ('next');
                /* Another comment with ; */
            """,
            )
            self.assertTrue(conn.in_transaction)
            self.assertEqual(
                conn.execute("SELECT value FROM audit").fetchall(),
                [
                    ("trigger;first",),
                    ("value;with;semicolons",),
                    ("trigger;first",),
                    ("next",),
                ],
            )
            conn.rollback()
            self.assertEqual(
                conn.execute("SELECT * FROM events").fetchall(), []
            )
            self.assertEqual(
                conn.execute("SELECT * FROM audit").fetchall(), []
            )
            self.assertEqual(
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall(),
                [],
            )

    def test_failure_rolls_back_prior_steps_without_an_implicit_commit(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.execute("CREATE TABLE events (value TEXT)")
            conn.execute("INSERT INTO events VALUES ('before')")
            conn.commit()
            with self.assertRaises(sqlite3.OperationalError):
                with conn:
                    conn.execute(
                        "INSERT INTO events VALUES ('owned transaction')"
                    )
                    execute_migration_script(
                        conn,
                        "INSERT INTO events VALUES ('script;value'); SELECT * FROM missing;",
                    )
            self.assertEqual(
                conn.execute("SELECT * FROM events").fetchall(), [("before",)]
            )

    def test_final_statement_can_omit_terminator(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            execute_migration_script(conn, "CREATE TABLE events (value TEXT)")
            execute_migration_script(
                conn, "INSERT INTO events VALUES ('last')"
            )
            self.assertEqual(
                conn.execute("SELECT * FROM events").fetchall(), [("last",)]
            )
