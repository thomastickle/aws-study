"""Synthetic variant groups cannot occupy two positions in one quiz."""

import copy
import sqlite3
from contextlib import redirect_stdout
from io import StringIO

from aws_study.cli import main

from aws_study.db import SCHEMA_VERSION, init_db
from aws_study.question_group_repository import QuestionGroupRepository
from aws_study.question_group_service import QuestionGroupService
from aws_study.quiz_repository import QuizRepository
from aws_study.quiz_service import QuizService
from aws_study.selection import Candidate, rank_candidates, weighted_sample
from study_fixture import BankTestCase, question


class SelectionGroupTests(BankTestCase):
    def setUp(self):
        super().setUp()
        first = question(1)
        variant = question(2)
        variant["question"] = "Synthetic equivalent wording?"
        variant["answers"][0]["text"] = "Different distractor"
        self.import_questions([first, variant, question(3)])
        self.groups = QuestionGroupService(QuestionGroupRepository(self.conn))
        self.groups.assign(1, [1, 2], key="confirmed-variants")
        self.quiz = QuizService(QuizRepository(self.conn))

    def test_every_strategy_avoids_variants_in_same_session(self):
        initial = self.quiz.create_session(
            1,
            count=3,
            target_year=2026,
            mode="exam",
            strategy="random",
            seed=0,
        )
        for q in self.quiz.questions(initial):
            self.quiz.record_answer(initial, q.id, {q.options[0].id}, "low", 0)
        for strategy in ("random", "adaptive", "weak", "new"):
            history = QuizRepository(self.conn).candidate_history(
                1, target_year=2026
            )
            pool = rank_candidates(history, strategy=strategy)
            for seed in range(50):
                picked = weighted_sample(pool, 20, seed=seed)
                ids = {c.question_id for c in picked}
                self.assertLessEqual(len(ids & {1, 2}), 1)
                self.assertEqual(len(ids), len(picked))
        for seed in range(20):
            sid = self.quiz.create_session(
                1,
                count=3,
                target_year=2026,
                mode="exam",
                strategy="random",
                seed=seed,
            )
            ids = {q.id for q in self.quiz.questions(sid)}
            self.assertEqual(len(ids), 2)
            self.assertIn(3, ids)
            self.assertLessEqual(len(ids & {1, 2}), 1)

    def test_unknown_or_other_certification_ids_cannot_partially_change_group(
        self,
    ):
        before = self.conn.serialize()
        with self.assertRaisesRegex(ValueError, "Unknown question IDs"):
            self.groups.assign(1, [1, 999], key="changed")
        self.assertEqual(self.conn.serialize(), before)
        self.import_questions([question()], cert_code="OTHER-C01")
        with self.assertRaisesRegex(ValueError, "Unknown question IDs"):
            self.groups.assign(1, [1, 4], key="changed")

    def test_group_metadata_imported_and_reimport_preserves_explicit_group(
        self,
    ):
        incoming = question(4)
        incoming["selection_group"] = "confirmed-variants"
        self.import_questions([incoming])
        self.assertEqual(
            self.conn.execute(
                "SELECT group_key FROM question_selection_groups WHERE question_id=4"
            ).fetchone()[0],
            "confirmed-variants",
        )
        incoming.pop("selection_group")
        self.import_questions([incoming])
        self.assertEqual(
            self.conn.execute(
                "SELECT group_key FROM question_selection_groups WHERE question_id=4"
            ).fetchone()[0],
            "confirmed-variants",
        )
        incoming["selection_group"] = "conflicting-name"
        with self.assertRaisesRegex(ValueError, "selection_group conflict"):
            self.import_questions([incoming])

    def test_group_keys_are_scoped_by_certification(self):
        q = question()
        q["selection_group"] = "confirmed-variants"
        self.import_questions([q], cert_code="OTHER-C01")
        history = QuizRepository(self.conn).candidate_history(
            2, target_year=2026
        )
        self.assertEqual(
            len(
                weighted_sample(
                    rank_candidates(history, strategy="random"), 20
                )
            ),
            1,
        )

    def test_all_records_stay_active_and_either_variant_can_be_selected(self):
        self.assertEqual(
            self.conn.execute(
                "SELECT SUM(is_active) FROM questions"
            ).fetchone()[0],
            3,
        )
        winners = set()
        for seed in range(50):
            sid = self.quiz.create_session(
                1,
                count=20,
                target_year=2026,
                mode="study",
                strategy="random",
                seed=seed,
            )
            winners.update(
                q.id for q in self.quiz.questions(sid) if q.id in (1, 2)
            )
        self.assertEqual(winners, {1, 2})

    def test_grouped_variants_preserve_independent_answers_and_history(self):
        before = {
            table: [
                tuple(r)
                for r in self.conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid"
                )
            ]
            for table in ("questions", "answers", "question_sources")
        }
        self.groups.assign(1, [1, 2], key="another-key")
        for table, rows in before.items():
            self.assertEqual(
                [
                    tuple(r)
                    for r in self.conn.execute(
                        f"SELECT * FROM {table} ORDER BY rowid"
                    )
                ],
                rows,
            )

    def test_normalized_same_stem_is_excluded_without_curated_group(self):
        first = question(4)
        second = copy.deepcopy(first)
        second["source_ref"] = "5"
        second["question"] = "  SYNTHETIC\u00a0 question   4? "
        second["answers"][0]["text"] = "Another distractor"
        self.import_questions([first, second])
        history = QuizRepository(self.conn).candidate_history(
            1, target_year=2026
        )
        for strategy in ("random", "adaptive", "new"):
            pool = rank_candidates(history, strategy=strategy)
            variants = set()
            for seed in range(50):
                selected = weighted_sample(pool, 20, seed)
                ids = {c.question_id for c in selected}
                self.assertLessEqual(len(ids & {4, 5}), 1)
                variants.update(ids & {4, 5})
            self.assertEqual(variants, {4, 5})

    def test_different_stems_in_same_topic_remain_independent(self):
        history = QuizRepository(self.conn).candidate_history(
            1, target_year=2026
        )
        selected = weighted_sample(
            rank_candidates(history, strategy="random"), 20, seed=1
        )
        self.assertEqual(len(selected), 2)
        self.assertIn(3, {c.question_id for c in selected})

    def test_explicit_group_cli_and_failure_rollback(self):
        with redirect_stdout(StringIO()) as output:
            main(
                [
                    "--db",
                    str(self.root / "study.db"),
                    "question-group",
                    "--cert",
                    "TEST-C01",
                    "--key",
                    "cli-variants",
                    "1",
                    "2",
                ]
            )
        self.assertIn(
            "Selection group cli-variants: questions 1, 2", output.getvalue()
        )
        before = [
            tuple(r)
            for r in self.conn.execute(
                "SELECT * FROM question_selection_groups ORDER BY question_id"
            )
        ]
        self.conn.execute(
            """CREATE TRIGGER reject_group BEFORE INSERT ON question_selection_groups
            WHEN NEW.question_id=2 BEGIN SELECT RAISE(ABORT,'Synthetic group failure'); END"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.groups.assign(1, [1, 2], key="changed")
        self.assertEqual(
            [
                tuple(r)
                for r in self.conn.execute(
                    "SELECT * FROM question_selection_groups ORDER BY question_id"
                )
            ],
            before,
        )


class SamplerTests(BankTestCase):
    def test_variant_choice_is_seeded_and_can_choose_either(self):
        pool = [
            Candidate(1, 1, "test", "group"),
            Candidate(2, 1, "test", "group"),
            Candidate(3, 1, "test"),
        ]
        winners = set()
        for seed in range(50):
            picked = weighted_sample(pool, 20, seed)
            self.assertEqual(picked, weighted_sample(pool, 20, seed))
            self.assertEqual(len(picked), 2)
            winners.update(c.question_id for c in picked if c.selection_group)
        self.assertEqual(winners, {1, 2})

    def test_duplicate_ids_and_zero_weights_cannot_repeat(self):
        pool = [
            Candidate(1, 0, "test"),
            Candidate(1, 0, "test"),
            Candidate(2, 0, "test"),
        ]
        self.assertEqual(len(weighted_sample(pool, 20, seed=1)), 2)

    def test_v2_upgrade_preserves_rows_and_is_idempotent(self):
        self.import_questions([question()])
        self.conn.execute("DROP TABLE question_selection_groups")
        self.conn.execute("PRAGMA user_version=2")
        self.conn.commit()
        before = [
            tuple(r) for r in self.conn.execute("SELECT * FROM questions")
        ]
        init_db(self.conn)
        self.assertEqual(
            self.conn.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            [tuple(r) for r in self.conn.execute("SELECT * FROM questions")],
            before,
        )
        after = self.conn.serialize()
        init_db(self.conn)
        self.assertEqual(self.conn.serialize(), after)
        self.assertEqual(
            self.conn.execute("PRAGMA foreign_key_check").fetchall(), []
        )
