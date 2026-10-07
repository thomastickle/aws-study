"""Constrained sampling fills feasible quizzes without dropping preferences."""

import itertools
import random
import unittest
from datetime import datetime, timezone

from aws_study.quiz_models import AttemptHistory, QuestionHistory
from aws_study.selection import Candidate, rank_candidates, weighted_sample


def valid_subset(items):
    ids = [item.question_id for item in items]
    groups = [
        item.selection_group
        for item in items
        if item.selection_group is not None
    ]
    stems = [
        item.normalized_stem
        for item in items
        if item.normalized_stem is not None
    ]
    return (
        len(ids) == len(set(ids))
        and len(groups) == len(set(groups))
        and len(stems) == len(set(stems))
    )


class ConstrainedSelectionTests(unittest.TestCase):
    def test_overlapping_constraints_replace_blocking_pick(self):
        items = [
            Candidate(1, 1000, "A", "G1", "S1"),
            Candidate(2, 1, "B", "G1", "S2"),
            Candidate(3, 1, "C", "G2", "S1"),
        ]
        for seed in range(50):
            with self.subTest(seed=seed):
                selected = weighted_sample(items, 2, seed)
                self.assertEqual(
                    {item.question_id for item in selected}, {2, 3}
                )
                self.assertEqual(selected, weighted_sample(items, 2, seed))
        self.assertEqual(
            {item.question_id for item in weighted_sample(items, 10, 1)},
            {2, 3},
        )

    def test_largest_feasible_sets_agree_with_exhaustive_small_pool_oracle(
        self,
    ):
        rng = random.Random(123)
        for case in range(100):
            pool = [
                Candidate(
                    index,
                    rng.choice([0, 1, 10]),
                    "oracle",
                    rng.choice([None, "G1", "G2", "G3"]),
                    rng.choice([None, "S1", "S2", "S3"]),
                )
                for index in range(rng.randrange(1, 9))
            ]
            maximum = max(
                size
                for size in range(len(pool) + 1)
                if any(
                    valid_subset(combo)
                    for combo in itertools.combinations(pool, size)
                )
            )
            for count in (0, 1, 3, len(pool) + 1):
                with self.subTest(case=case, count=count):
                    selected = weighted_sample(pool, count, case)
                    self.assertEqual(len(selected), min(count, maximum))
                    self.assertTrue(valid_subset(selected))
                    self.assertEqual(
                        selected, weighted_sample(pool, count, case)
                    )

    def test_weighted_preference_remains_strong(self):
        items = [
            Candidate(1, 100, "preferred", "G"),
            Candidate(2, 1, "other", "G"),
            Candidate(3, 1, "independent"),
        ]
        preferred = sum(
            1 in {item.question_id for item in weighted_sample(items, 2, seed)}
            for seed in range(200)
        )
        self.assertGreater(preferred, 185)

    def test_empty_negative_weights_and_repeated_ids(self):
        first = Candidate(1, -1, "first", "G1", "S1")
        items = [
            first,
            Candidate(1, 1000, "ignored duplicate", "G2", "S2"),
            Candidate(2, 0, "second", "G2", "S2"),
        ]
        self.assertEqual(weighted_sample([], 20, 1), [])
        self.assertEqual(
            set(item.question_id for item in weighted_sample(items, 20, 1)),
            {1, 2},
        )
        self.assertIn(first, weighted_sample(items, 20, 1))

    def test_injected_time_handles_boundaries_and_legacy_timestamps(self):
        now = datetime(2026, 10, 6, tzinfo=timezone.utc)
        history = [
            QuestionHistory(
                index, 1, 0, (AttemptHistory(True, None, attempted),)
            )
            for index, attempted in enumerate(
                [
                    "2026-09-29T00:00:00Z",
                    "2026-09-06T00:00:00",
                    "invalid",
                    None,
                    "2026-10-07T00:00:00+00:00",
                ],
                1,
            )
        ]
        ranked = rank_candidates(history, strategy="adaptive", now=now)
        self.assertEqual([item.weight for item in ranked], [2.5, 4, 1, 1, 1])
        with self.assertRaisesRegex(ValueError, "timezone"):
            rank_candidates(
                history, strategy="adaptive", now=datetime(2026, 10, 6)
            )
