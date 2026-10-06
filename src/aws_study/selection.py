from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .quiz_models import QuestionHistory


@dataclass
class Candidate:
    """A question's selection weight and the reasons contributing to it."""

    question_id: int
    weight: float
    reason: str
    selection_group: str | None = None
    normalized_stem: str | None = None


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def rank_candidates(
    history: Sequence[QuestionHistory],
    *,
    strategy: str,
    now: datetime | None = None,
) -> list[Candidate]:
    """Calculate selection weights from history without database access."""
    out: list[Candidate] = []
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    for question in history:
        qid = question.question_id
        n = question.attempt_count
        misses = question.miss_count
        if strategy == "random":
            out.append(
                Candidate(
                    qid,
                    1.0,
                    "random",
                    question.selection_group,
                    question.normalized_stem,
                )
            )
            continue
        if strategy == "new" and n > 0:
            continue
        if strategy == "weak" and misses == 0:
            continue

        weight = 1.0
        reasons: list[str] = []
        if n == 0:
            weight += 3.0
            reasons.append("new")
        else:
            miss_rate = misses / n
            if miss_rate >= 0.5:
                weight += 3.0
                reasons.append("high miss rate")
            elif misses:
                weight += 1.5
                reasons.append("previous miss")

            if question.recent_attempts:
                last = question.recent_attempts[0]
                if not last.is_correct:
                    weight += 5.0
                    reasons.append("last answer wrong")
                if last.confidence == "low":
                    weight += 2.0
                    reasons.append("low confidence")
                elif last.confidence == "medium":
                    weight += 0.75
                dt = _parse_dt(last.attempted_at)
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    days = (now - dt).total_seconds() / 86400
                    if days >= 30:
                        weight += 3.0
                        reasons.append("not seen 30d")
                    elif days >= 7:
                        weight += 1.5
                        reasons.append("not seen 7d")

            streak = 0
            for attempt in question.recent_attempts:
                if attempt.is_correct:
                    streak += 1
                else:
                    break
            if streak >= 3:
                weight *= 0.45
                reasons.append("3+ correct streak")

        out.append(
            Candidate(
                qid,
                max(0.2, weight),
                ", ".join(reasons) or "baseline",
                question.selection_group,
                question.normalized_stem,
            )
        )
    return out


def weighted_sample(
    items: list[Candidate],
    count: int,
    seed: int | None = None,
) -> list[Candidate]:
    """Prefer weighted picks; recover the largest feasible set if greedy underfills.

    Each question is an edge between its group and stem. An absent constraint
    gets a question-specific endpoint. Augmenting paths can replace a blocking
    pick without losing cardinality. Edge order remains weighted and seeded.
    Repeated question IDs use the first candidate's metadata and weight.
    """
    rng = random.Random(seed)
    unique: dict[int, Candidate] = {}
    for item in items:
        unique.setdefault(item.question_id, item)
    pool = list(unique.values())
    chosen: list[Candidate] = []
    while pool and len(chosen) < count:
        selected = pool.pop(_weighted_index(pool, rng))
        chosen.append(selected)
        pool = [
            candidate
            for candidate in pool
            if candidate.question_id != selected.question_id
            and (
                selected.selection_group is None
                or candidate.selection_group != selected.selection_group
            )
            and (
                selected.normalized_stem is None
                or candidate.normalized_stem != selected.normalized_stem
            )
        ]
    if len(chosen) >= count:
        return chosen
    # Start with preferred picks, then weighted alternatives that they excluded.
    preferred_ids = {item.question_id for item in chosen}
    remaining = [
        item
        for item in unique.values()
        if item.question_id not in preferred_ids
    ]
    priorities = list(chosen)
    while remaining:
        priorities.append(remaining.pop(_weighted_index(remaining, rng)))
    return _matching_sample(priorities, count)


def _weighted_index(pool: list[Candidate], rng: random.Random) -> int:
    total = sum(max(item.weight, 0.0) for item in pool)
    if total <= 0:
        return rng.randrange(len(pool))
    needle = rng.random() * total
    accumulated = 0.0
    for index, item in enumerate(pool):
        accumulated += max(item.weight, 0.0)
        if accumulated > needle:
            return index
    return len(pool) - 1


ConstraintKey = tuple[str, str | int]


def _group_key(item: Candidate) -> ConstraintKey:
    return (
        ("group", item.selection_group)
        if item.selection_group is not None
        else ("question", item.question_id)
    )


def _stem_key(item: Candidate) -> ConstraintKey:
    return (
        ("stem", item.normalized_stem)
        if item.normalized_stem is not None
        else ("question", item.question_id)
    )


def _matching_sample(items: list[Candidate], count: int) -> list[Candidate]:
    edges: dict[ConstraintKey, list[Candidate]] = {}
    for item in items:
        edges.setdefault(_group_key(item), []).append(item)
    matching: dict[ConstraintKey, Candidate] = {}

    def augment(group: ConstraintKey, visited: set[ConstraintKey]) -> bool:
        if group in visited:
            return False
        visited.add(group)
        for item in edges[group]:
            stem = _stem_key(item)
            previous = matching.get(stem)
            if previous is None or augment(_group_key(previous), visited):
                matching[stem] = item
                return True
        return False

    for group in edges:
        augment(group, set())
        if len(matching) >= count:
            break
    selected = {item.question_id for item in matching.values()}
    return [item for item in items if item.question_id in selected]
