from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone

from .selection import candidates, weighted_sample


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _options(conn: sqlite3.Connection, qid: int):
    return conn.execute(
        "SELECT * FROM options WHERE question_id=? ORDER BY option_order", (qid,)
    ).fetchall()


def _parse_answer(raw: str, labels: list[str]) -> set[int]:
    """Parse choices separated by any mix of whitespace, commas, or semicolons."""
    raw = raw.upper().replace(",", " ").replace(";", " ")
    tokens = raw.split()
    selected: set[int] = set()
    for token in tokens:
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(labels):
                selected.add(idx)
                continue
        if token in labels:
            selected.add(labels.index(token))
            continue
        raise ValueError(f"Unknown answer token: {token}")
    return selected


def _prompt_answer(labels: list[str]) -> set[int]:
    while True:
        try:
            selected = _parse_answer(input("Answer: "), labels)
            if not selected:
                raise ValueError("Choose at least one option.")
            return selected
        except ValueError as e:
            print(e)


def _prompt_confidence() -> str | None:
    raw = input(
        "Confidence [(C)onfident, (E)ducated Guess, (U)nsure, Enter to skip]: "
    ).strip().lower()
    return {
        "c": "high",
        "confident": "high",
        "e": "medium",
        "educated guess": "medium",
        "u": "low",
        "unsure": "low",
    }.get(raw)


def run_quiz(
    conn: sqlite3.Connection,
    cert_id: int,
    *,
    count: int,
    target_year: int | None,
    mode: str,
    strategy: str,
    seed: int | None,
    include_unverified: bool = False,
) -> int:
    pool = candidates(
        conn,
        cert_id,
        target_year=target_year,
        strategy=strategy,
        include_unverified=include_unverified,
    )
    if not pool:
        raise ValueError("No eligible questions. Check certification/year/verification filters.")
    picked = weighted_sample(pool, min(count, len(pool)), seed=seed)
    cur = conn.execute(
        """
        INSERT INTO sessions(certification_id, started_at, target_year, mode, strategy, requested_count, source_kind)
        VALUES (:certification_id, :started_at, :target_year, :mode, :strategy, :requested_count, 'interactive')
        """,
        {
            "certification_id": cert_id,
            "started_at": _now(),
            "target_year": target_year,
            "mode": mode,
            "strategy": strategy,
            "requested_count": count,
        },
    )
    session_id = int(cur.lastrowid)
    for pos, item in enumerate(picked, 1):
        conn.execute(
            """
            INSERT INTO session_questions(session_id, question_id, position, selection_weight)
            VALUES (:session_id, :question_id, :position, :selection_weight)
            """,
            {
                "session_id": session_id,
                "question_id": item.question_id,
                "position": pos,
                "selection_weight": item.weight,
            },
        )
    conn.commit()

    pending_results: list[tuple[int, bool, list[str], list[str]]] = []
    for pos, item in enumerate(picked, 1):
        q = conn.execute("SELECT * FROM questions WHERE id=?", (item.question_id,)).fetchone()
        opts = _options(conn, item.question_id)
        labels = [(o["option_label"] or chr(65+i)).upper() for i, o in enumerate(opts)]
        print(f"\n[{pos}/{len(picked)}] {q['question_text']}\n")
        for i, o in enumerate(opts):
            print(f"  {labels[i]}. {o['option_text']}")
        if q["question_type"] == "multi_select":
            print("  (Select all that apply; enter uppercase or lowercase letters separated by spaces, commas, or semicolons.)")

        start = time.monotonic()
        selected = _prompt_answer(labels)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        correct_set = {i for i, o in enumerate(opts) if o["is_correct"]}
        is_correct = selected == correct_set
        confidence = _prompt_confidence()

        cur = conn.execute(
            """
            INSERT INTO attempts(session_id, question_id, attempted_at, is_correct, confidence, elapsed_ms)
            VALUES (:session_id, :question_id, :attempted_at, :is_correct, :confidence, :elapsed_ms)
            """,
            {
                "session_id": session_id,
                "question_id": item.question_id,
                "attempted_at": _now(),
                "is_correct": int(is_correct),
                "confidence": confidence,
                "elapsed_ms": elapsed_ms,
            },
        )
        aid = int(cur.lastrowid)
        for i in selected:
            conn.execute(
                """
                INSERT INTO attempt_options(attempt_id, option_id, selected)
                VALUES (:attempt_id, :option_id, 1)
                """,
                {"attempt_id": aid, "option_id": opts[i]["id"]},
            )
        conn.commit()
        selected_texts = [f"{labels[i]}. {opts[i]['option_text']}" for i in sorted(selected)]
        correct_texts = [f"{labels[i]}. {opts[i]['option_text']}" for i in sorted(correct_set)]
        pending_results.append((item.question_id, is_correct, selected_texts, correct_texts))

        if mode == "study":
            print("✓ Correct" if is_correct else "✗ Incorrect")
            print("Correct answer(s): " + "; ".join(correct_texts))
            for i, o in enumerate(opts):
                if o["rationale"]:
                    print(f"  {labels[i]} rationale: {o['rationale']}")

    conn.execute(
        "UPDATE sessions SET completed_at=:completed_at WHERE id=:session_id",
        {"completed_at": _now(), "session_id": session_id},
    )
    conn.commit()

    correct_n = sum(1 for _, ok, _, _ in pending_results if ok)
    print(f"\nScore: {correct_n}/{len(pending_results)} ({(100*correct_n/len(pending_results)):.1f}%)")
    if mode == "exam":
        print("\nReview:")
        for i, (_, ok, selected, correct) in enumerate(pending_results, 1):
            if not ok:
                print(f"  Q{i}: ✗ selected {'; '.join(selected)} | correct {'; '.join(correct)}")
    return session_id
