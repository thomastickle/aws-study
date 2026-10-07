import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from study_fixture import BankTestCase, question

from aws_study.certification_repository import CertificationRepository
from aws_study.cli import main
from aws_study.db import connect, init_db
from aws_study.source_repository import SourceRepository
from aws_study.source_service import SourceService
from aws_study.statistics_repository import StatisticsRepository


class PersistenceTests(BankTestCase):
    def test_certification_upsert_preserves_omitted_metadata(self):
        repository = CertificationRepository(self.conn)
        with repository.transaction():
            first = repository.upsert(
                "TEST-C01",
                name="Synthetic",
                version="v1",
                active_from_year=2026,
                active_to_year=2028,
            )
            same = repository.upsert("TEST-C01", version="v2")
            other = repository.upsert("TEST-C01", provider="OTHER")
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        row = self.conn.execute(
            "SELECT * FROM certifications WHERE id=?",
            (first,),
        ).fetchone()
        self.assertEqual(row["name"], "Synthetic")
        self.assertEqual(row["version"], "v2")
        self.assertEqual(row["active_from_year"], 2026)
        self.assertEqual(row["active_to_year"], 2028)
        with self.assertRaisesRegex(ValueError, "Unknown certification"):
            repository.require_id("MISSING")

    def test_source_verification_updates_only_its_certification(self):
        self.import_questions([question()])
        self.import_questions([question()], cert_code="OTHER-C01")
        SourceService(SourceRepository(self.conn)).verify(
            1,
            "TEST-C01",
            "pretest",
            year=2027,
            status="verified_current",
        )
        rows = self.conn.execute(
            "SELECT verified_year,verification_status FROM question_sources ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [tuple(r) for r in rows],
            [(2027, "verified_current"), (2026, "official_current")],
        )

    def test_source_verification_failure_rolls_back_source_and_provenance(
        self,
    ):
        self.import_questions([question()])
        self.conn.execute(
            """CREATE TRIGGER reject_verification BEFORE UPDATE ON question_sources
            BEGIN SELECT RAISE(ABORT, 'Synthetic verification failure'); END"""
        )
        service = SourceService(SourceRepository(self.conn))
        with self.assertRaises(sqlite3.IntegrityError):
            service.verify(
                1, "TEST-C01", "pretest", year=2027, status="verified_current"
            )
        self.assertEqual(
            tuple(
                self.conn.execute(
                    "SELECT verification_origin,verified_at FROM sources"
                ).fetchone()
            ),
            ("import", "2026-01-01"),
        )
        with self.assertRaisesRegex(ValueError, "Unknown source key"):
            service.verify(
                1, "TEST-C01", "missing", year=2027, status="verified_current"
            )
        with self.assertRaisesRegex(ValueError, "Unknown verification status"):
            service.verify(
                1, "TEST-C01", "pretest", year=2027, status="unknown"
            )

    def test_statistics_use_curated_area_and_topic_and_isolate_certifications(
        self,
    ):
        self.import_questions([question(), question(2)])
        self.import_questions([question()], cert_code="OTHER-C01")
        from aws_study.quiz_repository import QuizRepository
        from aws_study.quiz_service import QuizService

        service = QuizService(QuizRepository(self.conn))
        sid = service.create_session(
            1,
            count=2,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=1,
        )
        for q in service.questions(sid):
            service.record_answer(
                sid,
                q.id,
                {next(o.id for o in q.options if not o.correct)},
                None,
                0,
            )
        self.conn.execute("UPDATE questions SET is_active=0 WHERE id=2")
        self.conn.commit()
        stats = StatisticsRepository(self.conn).for_certification(1)
        self.assertEqual(
            (stats.active_questions, stats.attempts, stats.correct), (1, 2, 0)
        )
        self.assertEqual(
            [
                (t.area, t.topic, t.attempts, t.misses)
                for t in stats.weak_topics
            ],
            [("Compute", "Synthetic topic", 2, 2)],
        )
        sources = SourceRepository(self.conn).summaries(1)
        self.assertEqual(
            (sources[0].question_count, sources[0].canonical_count), (2, 2)
        )
        self.assertEqual(
            StatisticsRepository(self.conn).for_certification(2).attempts, 0
        )

    def test_empty_statistics_and_cli_output(self):
        with redirect_stdout(StringIO()):
            main(
                [
                    "--db",
                    str(self.root / "study.db"),
                    "cert-add",
                    "--code",
                    "EMPTY-C01",
                ]
            )
        with redirect_stdout(StringIO()) as output:
            main(
                [
                    "--db",
                    str(self.root / "study.db"),
                    "stats",
                    "--cert",
                    "EMPTY-C01",
                ]
            )
        self.assertIn("0 canonical active questions", output.getvalue())
        self.assertIn("Attempts: 0; correct: 0", output.getvalue())

    def test_source_commands_preserve_display_and_verified_year(self):
        self.import_questions([question()])
        prefix = ["--db", str(self.root / "study.db")]
        with redirect_stdout(StringIO()) as output:
            main(prefix + ["sources", "--cert", "TEST-C01"])
        self.assertIn("questions=1 (1 canonical)", output.getvalue())
        with redirect_stdout(StringIO()):
            main(
                prefix
                + [
                    "source-verify",
                    "--cert",
                    "TEST-C01",
                    "--source-key",
                    "pretest",
                    "--year",
                    "2027",
                ]
            )
        self.assertEqual(
            self.conn.execute(
                "SELECT verified_year FROM question_sources"
            ).fetchone()[0],
            2027,
        )


class ConnectionLifetimeTests(unittest.TestCase):
    def test_cli_closes_connections_on_success_and_errors(self):
        with tempfile.TemporaryDirectory() as root:
            for command, initialization_error in (
                (["init"], False),
                (["stats", "--cert", "MISSING"], False),
                (["init"], True),
            ):
                with self.subTest(command=command, error=initialization_error):
                    conn = connect(Path(root) / "study.db")
                    with (
                        patch("aws_study.cli.connect", return_value=conn),
                        patch(
                            "aws_study.cli.init_db",
                            side_effect=(
                                sqlite3.OperationalError(
                                    "Synthetic schema failure"
                                )
                                if initialization_error
                                else lambda c: init_db(c)
                            ),
                        ),
                        redirect_stdout(StringIO()),
                        redirect_stderr(StringIO()),
                    ):
                        if initialization_error or command[0] == "stats":
                            with self.assertRaises(SystemExit) as error:
                                main(command)
                            self.assertEqual(error.exception.code, 2)
                        else:
                            main(command)
                    with self.assertRaises(sqlite3.ProgrammingError):
                        conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
