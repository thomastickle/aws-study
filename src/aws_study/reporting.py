from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _without_letter(text: str) -> str:
    if len(text) > 3 and text[1:3] == ". ":
        return text[3:]
    return text


def _gap_concept(a: dict) -> str:
    selected = [_without_letter(x) for x in (a.get("selected") or [])]
    correct = [_without_letter(x) for x in (a.get("correct") or [])]
    terms = []
    for x in selected + correct:
        if x not in terms:
            terms.append(x)
    if 1 < len(terms) <= 4 and all(len(x) <= 90 for x in terms):
        return " vs ".join(terms)
    return a.get("concept") or a.get("topic") or a.get("domain") or "General AWS"


def _selected_options(conn: sqlite3.Connection, attempt_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT COALESCE(o.option_label || '. ', '') || o.option_text AS text
        FROM attempt_options ao JOIN options o ON o.id=ao.option_id
        WHERE ao.attempt_id=? AND ao.selected=1 ORDER BY o.option_order
        """,
        (attempt_id,),
    ).fetchall()
    return [r["text"] for r in rows]


def _correct_options(conn: sqlite3.Connection, question_id: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT COALESCE(option_label || '. ', '') || option_text AS text
        FROM options WHERE question_id=? AND is_correct=1 ORDER BY option_order
        """,
        (question_id,),
    ).fetchall()
    return [r["text"] for r in rows]


def session_data(conn: sqlite3.Connection, session_id: int) -> dict:
    s = conn.execute(
        """
        SELECT s.*, c.provider, c.code, c.name cert_name
        FROM sessions s JOIN certifications c ON c.id=s.certification_id WHERE s.id=?
        """,
        (session_id,),
    ).fetchone()
    if not s:
        raise ValueError(f"Unknown session {session_id}")
    attempts = conn.execute(
        """
        SELECT a.*, q.question_text, q.topic, q.concept, q.domain, q.external_key,
               src.name source_name, src.source_key, q.source_question_number
        FROM attempts a
        JOIN questions q ON q.id=a.question_id
        JOIN sources src ON src.id=q.source_id
        WHERE a.session_id=? ORDER BY a.id
        """,
        (session_id,),
    ).fetchall()
    items = []
    for a in attempts:
        d = dict(a)
        d["selected"] = _selected_options(conn, a["id"])
        d["correct"] = _correct_options(conn, a["question_id"])
        items.append(d)
    return {"session": dict(s), "attempts": items}


def continuation_prompt(data: dict, *, include_low_confidence: bool = True) -> str:
    s = data["session"]
    misses = [a for a in data["attempts"] if not a["is_correct"]]
    low = [a for a in data["attempts"] if a["is_correct"] and a.get("confidence") == "low"]
    focus = misses + (low if include_low_confidence else [])
    lines = [
        f"I'm studying for {s['provider']} {s['code']}" + (f" ({s['cert_name']})" if s.get('cert_name') else "") + ".",
        f"Target study year: {s.get('target_year') or 'current'}.",
        "",
        "Use reasoning-first coaching. Ask one question at a time and let me explain my reasoning before revealing the answer. Do not reveal or hint at answers from prior attempts before I respond.",
        "Discuss why each distractor is wrong and sharpen boundaries between neighboring services/concepts. Mix weak areas with unrelated material so the topic itself does not reveal the answer.",
        "",
        "Latest weak/reinforcement areas:",
    ]
    if not focus:
        lines.append("- No misses in the latest session. Use broad mixed review and older weak areas if available.")
    for a in focus:
        area = a.get("topic") or a.get("domain") or "General AWS"
        concept = _gap_concept(a)
        selected = "; ".join(a.get("selected") or ["(no answer)"])
        correct = "; ".join(a.get("correct") or [])
        kind = "LOW-CONFIDENCE CORRECT" if a["is_correct"] else "MISSED"
        lines.append(f"- {kind} [{area}] {concept}: selected {selected}; correct {correct}.")
    lines += [
        "",
        "Question-source rule: exact question wording/answers must come from the attached/local question bank, not from this summary. Treat this prompt as learning-state metadata only.",
    ]
    return "\n".join(lines)


def write_report_bundle(conn: sqlite3.Connection, session_id: int, out_dir: str | Path) -> dict[str, Path]:
    data = session_data(conn, session_id)
    s = data["session"]
    attempts = data["attempts"]
    correct_n = sum(1 for a in attempts if a["is_correct"])
    total = len(attempts)
    score = (100 * correct_n / total) if total else 0.0
    misses = [a for a in attempts if not a["is_correct"]]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"session-{session_id}-{stamp}"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    md = [
        f"# {s['provider']} {s['code']} Mini Exam Report",
        "",
        f"- Session: {session_id}",
        f"- Target year: {s.get('target_year') or 'current'}",
        f"- Mode: {s.get('mode')}",
        f"- Strategy: {s.get('strategy')}",
        f"- Score: {correct_n}/{total} ({score:.1f}%)",
        "",
        "## Missed areas",
        "",
    ]
    if not misses:
        md.append("No missed questions in this session.")
    for idx, a in enumerate(misses, start=1):
        area = a.get("topic") or a.get("domain") or "General AWS"
        selected = "; ".join(a.get("selected") or ["(no answer)"])
        correct = "; ".join(a.get("correct") or [])
        md += [
            f"### {idx}. {area}",
            f"- Source: {a['source_name']} #{a.get('source_question_number') or a.get('external_key')}",
            f"- Concept: {_gap_concept(a)}",
            f"- Selected: {selected}",
            f"- Correct: {correct}",
            f"- Confidence: {a.get('confidence') or 'not recorded'}",
            "",
        ]
    prompt = continuation_prompt(data)
    md += ["## Compact ChatGPT continuation prompt", "", "```text", prompt, "```", ""]

    report_path = out / f"{base}-report.md"
    prompt_path = out / f"{base}-prompt.txt"
    context_path = out / f"{base}-context.json"
    report_path.write_text("\n".join(md), encoding="utf-8")
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    # Small context pack: only misses/low-confidence items, including exact local source text.
    focus_ids = {
        a["question_id"]
        for a in attempts
        if (not a["is_correct"]) or a.get("confidence") == "low"
    }
    pack_questions = []
    for qid in focus_ids:
        q = conn.execute(
            """
            SELECT q.*, s.name source_name, s.source_type, s.observed_year, s.verification_status source_verification
            FROM questions q JOIN sources s ON s.id=q.source_id WHERE q.id=?
            """,
            (qid,),
        ).fetchone()
        opts = conn.execute(
            "SELECT option_order, option_label, option_text, is_correct, rationale FROM options WHERE question_id=? ORDER BY option_order",
            (qid,),
        ).fetchall()
        qd = dict(q)
        qd["options"] = [dict(x) for x in opts]
        pack_questions.append(qd)
    context = {
        "schema_version": 1,
        "purpose": "Minimal session context pack; local question bank remains authoritative.",
        "session": s,
        "continuation_prompt": prompt,
        "questions": pack_questions,
    }
    context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return {"report": report_path, "prompt": prompt_path, "context": context_path}
