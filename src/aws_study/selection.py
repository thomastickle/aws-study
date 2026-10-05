from __future__ import annotations

import math
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Candidate:
    question_id: int
    weight: float
    reason: str


def _parse_dt(s: str | None):
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
    where = ["q.certification_id=?", "q.is_active=1"]
    params: list[object] = [cert_id]
    if canonical_only:
        where.append("q.dedup_role='canonical'")
    if target_year is not None:
        where.append("(q.valid_from_year IS NULL OR q.valid_from_year<=?)")
        where.append("(q.valid_to_year IS NULL OR q.valid_to_year>=?)")
        params.extend([target_year, target_year])
    if not include_unverified:
        where.append("COALESCE(q.verification_status, 'unverified') IN ('official_current','verified_current')")
        # Freshness is explicit: a question verified in 2026 is not automatically
        # treated as verified-current for a 2027 target exam. Reverify/reimport it,
        # or opt in with --include-unverified after making that decision consciously.
        if target_year is not None:
            where.append("q.verified_year IS NOT NULL AND q.verified_year>=?")
            params.append(target_year)

    rows = conn.execute(
        f"""
        SELECT q.id,
               COUNT(a.id) attempts,
               SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END) misses,
               MAX(a.id) last_attempt_id,
               MAX(a.attempted_at) last_at
          FROM questions q
          LEFT JOIN attempts a ON a.question_id=q.id
         WHERE {' AND '.join(where)}
         GROUP BY q.id
        """,
        params,
    ).fetchall()

    out: list[Candidate] = []
    now = datetime.now(timezone.utc)
    for r in rows:
        qid = int(r["id"])
        n = int(r["attempts"] or 0)
        misses = int(r["misses"] or 0)
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

            last = conn.execute(
                "SELECT is_correct, confidence, attempted_at FROM attempts WHERE question_id=? ORDER BY id DESC LIMIT 1",
                (qid,),
            ).fetchone()
            if last:
                if not last["is_correct"]:
                    weight += 5.0
                    reasons.append("last answer wrong")
                if last["confidence"] == "low":
                    weight += 2.0
                    reasons.append("low confidence")
                elif last["confidence"] == "medium":
                    weight += 0.75
                dt = _parse_dt(last["attempted_at"])
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
            for a in conn.execute(
                "SELECT is_correct, confidence FROM attempts WHERE question_id=? ORDER BY id DESC LIMIT 5",
                (qid,),
            ):
                if a["is_correct"]:
                    streak += 1
                else:
                    break
            if streak >= 3:
                weight *= 0.45
                reasons.append("3+ correct streak")

        out.append(Candidate(qid, max(0.2, weight), ", ".join(reasons) or "baseline"))
    return out


def weighted_sample(items: list[Candidate], count: int, seed: int | None = None) -> list[Candidate]:
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
