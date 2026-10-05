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
    raw = raw.strip().upper().replace(",", " ")
    tokens = [t for t in raw.split() if t]
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
        VALUES (?,?,?,?,?,?, 'interactive')
        """,
        (cert_id, _now(), target_year, mode, strategy, count),
    )
    session_id = int(cur.lastrowid)
    for pos, item in enumerate(picked, 1):
        conn.execute(
            "INSERT INTO session_questions(session_id, question_id, position, selection_weight) VALUES (?,?,?,?)",
            (session_id, item.question_id, pos, item.weight),
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
            print("  (Select all that apply; enter letters separated by spaces.)")

        start = time.monotonic()
        while True:
            try:
                raw = input("Answer: ")
                selected = _parse_answer(raw, labels)
                if not selected:
                    raise ValueError("Choose at least one option.")
                break
            except ValueError as e:
                print(e)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        correct_set = {i for i, o in enumerate(opts) if o["is_correct"]}
        is_correct = selected == correct_set
        confidence = None
        raw_conf = input("Confidence [l/m/h, Enter to skip]: ").strip().lower()
        confidence = {"l": "low", "m": "medium", "h": "high"}.get(raw_conf)

        cur = conn.execute(
            """
            INSERT INTO attempts(session_id, question_id, attempted_at, is_correct, confidence, elapsed_ms)
            VALUES (?,?,?,?,?,?)
            """,
            (session_id, item.question_id, _now(), 1 if is_correct else 0, confidence, elapsed_ms),
        )
        aid = int(cur.lastrowid)
        for i in selected:
            conn.execute(
                "INSERT INTO attempt_options(attempt_id, option_id, selected) VALUES (?,?,1)",
                (aid, opts[i]["id"]),
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

    conn.execute("UPDATE sessions SET completed_at=? WHERE id=?", (_now(), session_id))
    conn.commit()

    correct_n = sum(1 for _, ok, _, _ in pending_results if ok)
    print(f"\nScore: {correct_n}/{len(pending_results)} ({(100*correct_n/len(pending_results)):.1f}%)")
    if mode == "exam":
        print("\nReview:")
        for i, (_, ok, selected, correct) in enumerate(pending_results, 1):
            if not ok:
                print(f"  Q{i}: ✗ selected {'; '.join(selected)} | correct {'; '.join(correct)}")
    return session_id
