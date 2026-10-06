"""Unexpected persistence failures remain explicit under optimized Python."""

from unittest.mock import Mock, patch

from study_fixture import BankTestCase, question

from aws_study.repository import SQLiteRepository
from aws_study.source_repository import SourceRepository


class RuntimeCheckTests(BankTestCase):
    def test_missing_source_after_upsert_raises_and_rolls_back(self):
        self.import_questions([question()])
        repo = SourceRepository(self.conn)
        with patch.object(repo, "find_id", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "could not be read"):
                with repo.transaction():
                    repo.upsert(
                        1,
                        "missing",
                        name="Synthetic",
                        source_type="synthetic",
                        source_file="test.json",
                        observed_year=None,
                        verification_status="unverified",
                        verified_at=None,
                    )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM sources WHERE source_key='missing'"
            ).fetchone()[0],
            0,
        )

    def test_insert_without_row_id_is_an_explicit_error(self):
        with self.assertRaisesRegex(RuntimeError, "row ID"):
            SQLiteRepository.inserted_id(Mock(lastrowid=None))
