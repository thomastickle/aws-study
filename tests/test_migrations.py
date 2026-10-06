"""Synthetic legacy history exercises remapping and non-destructive failure."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from aws_study.db import SCHEMA_VERSION, connect, init_db
from aws_study.migrations import migrate_v1_to_v2
from aws_study.report_repository import ReportRepository
from aws_study.report_service import ReportService
from aws_study.source_repository import SourceRepository


def legacy_fixture(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        (Path(__file__).parent / "fixtures/schema_v1.sql").read_text()
    )
    conn.execute("INSERT INTO certifications(id,code) VALUES (1,'TEST-C01')")
    for sid, key in [(1, "pretest"), (2, "practice_exam")]:
        conn.execute(
            """INSERT INTO sources(id,certification_id,source_key,name,observed_year,
            verification_status,verified_at) VALUES (?,1,?,?,2026,'official_current','2026-01-01')""",
            (sid, key, key),
        )
    for qid, sid, kind, stem in [
        (1, 1, "single_select", "Question one?"),
        (2, 2, "single_select", " question ONE? "),
        (3, 1, "multi_select", "Question three?"),
    ]:
        conn.execute(
            """INSERT INTO questions(id,certification_id,source_id,external_key,
            source_question_number,question_text,question_type,topic,concept,content_hash,
            valid_from_year,verification_status,verified_year)
            VALUES (?,1,?,?,?, ?,?,'Bad heuristic','Bad concept','obsolete hash',2026,'official_current',2026)""",
            (qid, sid, str(qid), qid, stem, kind),
        )
    options = [
        (11, 1, 1, "Wrong", 0),
        (12, 1, 2, "Right", 1),
        (21, 2, 1, "RIGHT", 1),
        (22, 2, 2, "wrong", 0),
        (31, 3, 1, "First", 1),
        (32, 3, 2, "Second", 1),
        (33, 3, 3, "Third", 0),
    ]
    for oid, qid, order, text, correct in options:
        conn.execute(
            """INSERT INTO options(id,question_id,option_order,option_label,option_text,is_correct,rationale)
            VALUES (?,?,?,?,?,?,?)""",
            (
                oid,
                qid,
                order,
                chr(64 + order),
                text,
                correct,
                f"Source rationale {oid}",
            ),
        )
    for sid, kind in [(1, "interactive"), (2, "imported_baseline")]:
        conn.execute(
            """INSERT INTO sessions(id,certification_id,started_at,completed_at,target_year,
            source_kind,session_label) VALUES (?,1,'2026-01-01','2026-01-02',2026,?,?)""",
            (sid, kind, kind),
        )
    for sid, qid, pos in [(1, 1, 1), (1, 3, 2), (2, 2, 1)]:
        conn.execute(
            "INSERT INTO session_questions VALUES (?,?,?,2.5)", (sid, qid, pos)
        )
    for aid, sid, qid, correct, confidence in [
        (1, 1, 1, 0, "low"),
        (2, 1, 3, 1, "medium"),
        (3, 2, 2, 1, "high"),
    ]:
        conn.execute(
            """INSERT INTO attempts(id,session_id,question_id,attempted_at,is_correct,
            confidence,elapsed_ms,source_kind,note) VALUES (?,?,?,'2026-01-01',?,?,1234,?,?)""",
            (
                aid,
                sid,
                qid,
                correct,
                confidence,
                "imported_baseline" if sid == 2 else "interactive",
                f"Note {aid}",
            ),
        )
    for aid, oid in [(1, 11), (2, 31), (2, 32), (3, 21)]:
        conn.execute("INSERT INTO attempt_options VALUES (?,?,1)", (aid, oid))
    for qid in (1, 2, None):
        conn.execute(
            "INSERT INTO review_notes(question_id,certification_id,note_text) VALUES (?,1,'Keep this note')",
            (qid,),
        )
    conn.commit()
    return conn


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "old.db"
        self.dest = self.root / "new.db"
        self.old = legacy_fixture(self.source)
        self.addCleanup(self.old.close)

    def test_history_and_source_specific_data_survive_merge(self):
        before = self.source.read_bytes()
        summary = migrate_v1_to_v2(self.source, self.dest)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(summary["canonical_questions"], 2)
        self.assertEqual(summary["provenance_rows"], 3)
        self.assertEqual(summary["sessions_preserved"], 2)
        self.assertEqual(summary["attempts_preserved"], 3)
        self.assertEqual(summary["selected_answer_records_preserved"], 4)
        self.assertEqual(summary["review_notes_preserved"], 3)
        conn = connect(self.dest)
        self.addCleanup(conn.close)
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )
        self.assertEqual(
            conn.execute("PRAGMA foreign_key_check").fetchall(), []
        )
        self.assertEqual(
            [
                tuple(r)
                for r in conn.execute("SELECT area,topic FROM questions")
            ],
            [(None, None)] * 2,
        )
        attempts = [
            dict(r) for r in conn.execute("SELECT * FROM attempts ORDER BY id")
        ]
        expected = [
            dict(
                zip(
                    [
                        d[0]
                        for d in self.old.execute(
                            "SELECT * FROM attempts"
                        ).description
                    ],
                    r,
                )
            )
            for r in self.old.execute("SELECT * FROM attempts ORDER BY id")
        ]
        expected[2]["question_id"] = 1
        self.assertEqual(attempts, expected)
        notes = [
            r[0]
            for r in conn.execute(
                "SELECT question_id FROM review_notes ORDER BY id"
            )
        ]
        self.assertEqual(notes, [1, 1, None])
        baseline = ReportService(ReportRepository(conn)).session_data(2)
        self.assertEqual(baseline["attempts"][0]["selected"], ["A. Right"])
        interactive = ReportService(ReportRepository(conn)).session_data(1)
        self.assertEqual(interactive["attempts"][0]["selected"], ["A. Wrong"])
        self.assertEqual(interactive["attempts"][0]["correct"], ["B. Right"])
        sources = ReportRepository(conn).question_context(1)["sources"]
        self.assertEqual(len(sources), 2)
        self.assertEqual(sources[1]["answers"][0]["answer_text"], "Right")
        self.assertEqual(
            sources[1]["answers"][0]["rationale"], "Source rationale 21"
        )
        self.assertEqual(sources[1]["answers"][1]["source_order"], 2)
        self.assertEqual(
            SourceRepository(conn).summaries(1)[0].key, "official-pretest-2026"
        )

    def test_conflicting_answer_key_aborts_and_publishes_no_destination(self):
        self.old.execute(
            "UPDATE options SET is_correct=1-is_correct WHERE question_id=2"
        )
        self.old.commit()
        with self.assertRaisesRegex(ValueError, "answer_key_fingerprint"):
            migrate_v1_to_v2(self.source, self.dest)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob(".aws-study-migration-*")), [])

    def test_session_collision_is_not_silently_dropped(self):
        self.old.execute("INSERT INTO session_questions VALUES (1,2,3,1)")
        self.old.commit()
        with self.assertRaisesRegex(ValueError, "collapse two questions"):
            migrate_v1_to_v2(self.source, self.dest)
        self.assertFalse(self.dest.exists())

    def test_stored_correctness_mismatch_aborts(self):
        self.old.execute("UPDATE attempts SET is_correct=1 WHERE id=1")
        self.old.commit()
        with self.assertRaisesRegex(ValueError, "correctness mismatch"):
            migrate_v1_to_v2(self.source, self.dest)
        self.assertFalse(self.dest.exists())

    def test_duplicate_normalized_answers_abort(self):
        self.old.execute(
            "UPDATE options SET option_text=' RIGHT ' WHERE id=11"
        )
        self.old.commit()
        with self.assertRaisesRegex(ValueError, "duplicate normalized answer"):
            migrate_v1_to_v2(self.source, self.dest)
        self.assertFalse(self.dest.exists())

    def test_existing_destination_and_source_are_never_overwritten(self):
        self.dest.write_text("Keep me")
        for dest in (self.dest, self.source):
            with self.assertRaisesRegex(ValueError, "already exists"):
                migrate_v1_to_v2(self.source, dest)
        self.assertEqual(self.dest.read_text(), "Keep me")

    def test_init_rejects_legacy_without_modifying_it(self):
        self.old.row_factory = sqlite3.Row
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, "Legacy database"):
            init_db(self.old)
        with self.assertRaisesRegex(ValueError, "Legacy database"):
            connect(self.source)
        self.assertEqual(before, self.source.read_bytes())

    def test_newer_schema_is_rejected(self):
        self.old.execute("PRAGMA user_version=99")
        with self.assertRaisesRegex(ValueError, "Unsupported database schema"):
            migrate_v1_to_v2(self.source, self.dest)

    def test_init_new_schema_enables_foreign_keys_and_is_idempotent(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        init_db(conn)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        before = conn.serialize()
        init_db(conn)
        self.assertEqual(conn.serialize(), before)
        self.assertEqual(
            conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO answers(question_id,answer_text,normalized_text,is_correct,display_order) VALUES (999,'A','a',1,1)"
            )

    def test_incomplete_v2_schema_fails_without_modification(self):
        conn = sqlite3.connect(self.dest)
        self.addCleanup(conn.close)
        init_db(conn)
        conn.execute(
            "ALTER TABLE answers RENAME COLUMN answer_text TO missing_text"
        )
        conn.commit()
        before = self.dest.read_bytes()
        with self.assertRaisesRegex(ValueError, "Incomplete v2 schema"):
            init_db(conn)
        self.assertEqual(self.dest.read_bytes(), before)
