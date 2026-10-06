from __future__ import annotations

import random
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from .quiz_models import QuestionHistory
from .quiz_repository import QuizRepository


@dataclass
class Candidate:
    """A question's selection weight and the reasons contributing to it."""

    question_id: int
    weight: float
    reason: str


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def candidates(
    conn: sqlite3.Connection,
    cert_id: int,
    *,
    target_year: int | None,
    strategy: str,
    include_unverified: bool = False,
    canonical_only: bool = True,
) -> list[Candidate]:
    """Load and rank candidates using the existing connection-based API."""
    history = QuizRepository(conn).candidate_history(
        cert_id, target_year=target_year,
        include_unverified=include_unverified, canonical_only=canonical_only,
        include_recent=strategy not in ("random", "new"),
    )
    return rank_candidates(history, strategy=strategy)


def rank_candidates(
    history: Sequence[QuestionHistory], *, strategy: str,
) -> list[Candidate]:
    """Calculate selection weights from history without database access."""
    out: list[Candidate] = []
    now = datetime.now(timezone.utc)
    for question in history:
        qid = question.question_id
        n = question.attempt_count
        misses = question.miss_count
        if strategy == "random":
            out.append(Candidate(qid, 1.0, "random"))
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

        out.append(Candidate(
            qid, max(0.2, weight), ", ".join(reasons) or "baseline",
        ))
    return out


def weighted_sample(
    items: list[Candidate], count: int, seed: int | None = None,
) -> list[Candidate]:
    """Pick weighted questions without replacement, optionally reproducibly."""
    rng = random.Random(seed)
    pool = list(items)
    chosen: list[Candidate] = []
    while pool and len(chosen) < count:
        total = sum(max(x.weight, 0.0) for x in pool)
        if total <= 0:
            pick = rng.randrange(len(pool))
        else:
            needle = rng.random() * total
            acc = 0.0
            pick = len(pool) - 1
            for i, item in enumerate(pool):
                acc += max(item.weight, 0.0)
                if acc >= needle:
                    pick = i
                    break
        chosen.append(pool.pop(pick))
    return chosen
