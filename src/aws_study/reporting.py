"""Render report text and write prepared bundles to disk."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .report_models import JsonRecord, ReportBundle, SessionData

DEFAULT_REPORT_DIR = Path("private/reports")


def _create_report_directory(session: JsonRecord, out_dir: str | Path) -> Path:
    """Create a timestamped export folder beneath its certification code."""
    code = re.sub(r"[^A-Za-z0-9_-]+", "-", session["code"]).strip("-")
    code = code or "unknown"
    root = Path(out_dir) / code
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = 1
    while True:
        directory = root / (stamp if suffix == 1 else f"{stamp}-{suffix}")
        try:
            directory.mkdir()
        except FileExistsError:
            suffix += 1
        else:
            return directory


def _without_letter(text: str) -> str:
    if len(text) > 3 and text[1:3] == ". ":
        return text[3:]
    return text


def _gap_concept(a: JsonRecord) -> str:
    selected = [_without_letter(x) for x in (a.get("selected") or [])]
    correct = [_without_letter(x) for x in (a.get("correct") or [])]
    terms = []
    for x in selected + correct:
        if x not in terms:
            terms.append(x)
    if 1 < len(terms) <= 4 and all(len(x) <= 90 for x in terms):
        return " vs ".join(terms)
    return (
        a.get("concept") or a.get("topic") or a.get("domain") or "General AWS"
    )


def continuation_prompt(
    data: SessionData, *, include_low_confidence: bool = True,
) -> str:
    """Render reinforcement guidance from prepared session/attempt records."""
    s = data["session"]
    misses = [a for a in data["attempts"] if not a["is_correct"]]
    low = [a for a in data["attempts"]
           if a["is_correct"] and a.get("confidence") == "low"]
    focus = misses + (low if include_low_confidence else [])
    lines = [
        f"I'm studying for {s['provider']} {s['code']}"
        + (f" ({s['cert_name']})" if s.get('cert_name') else "") + ".",
        f"Target study year: {s.get('target_year') or 'current'}.",
        "",
        "Use reasoning-first coaching. Ask one question at a time and let me "
        "explain my reasoning before revealing the answer. Do not reveal or "
        "hint at answers from prior attempts before I respond.",
        "Discuss why each distractor is wrong and sharpen boundaries between "
        "neighboring services/concepts. Mix weak areas with unrelated "
        "material so the topic itself does not reveal the answer.",
        "",
        "Latest weak/reinforcement areas:",
    ]
    if not focus:
        lines.append(
            "- No misses in the latest session. Use broad mixed review "
            "and older weak areas if available."
        )
    for a in focus:
        area = a.get("topic") or a.get("domain") or "General AWS"
        concept = _gap_concept(a)
        selected = "; ".join(a.get("selected") or ["(no answer)"])
        correct = "; ".join(a.get("correct") or [])
        kind = "LOW-CONFIDENCE CORRECT" if a["is_correct"] else "MISSED"
        lines.append(
            f"- {kind} [{area}] {concept}: selected {selected}; "
            f"correct {correct}."
        )
    lines += [
        "",
        "Question-source rule: exact question wording/answers must come from "
        "the attached/local question bank, not from this summary. Treat this "
        "prompt as learning-state metadata only.",
    ]
    return "\n".join(lines)


def write_report_bundle(
    bundle: ReportBundle,
    out_dir: str | Path = DEFAULT_REPORT_DIR,
) -> dict[str, Path]:
    """Render a prepared bundle; no database access is needed here."""
    data = bundle.data
    s = data["session"]
    session_id = s["id"]
    attempts = data["attempts"]
    correct_n = sum(1 for a in attempts if a["is_correct"])
    total = len(attempts)
    score = (100 * correct_n / total) if total else 0.0
    misses = [a for a in attempts if not a["is_correct"]]
    out = _create_report_directory(s, out_dir)

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
            f"- Source: {a['source_name']} "
            f"#{a.get('source_question_number') or a.get('external_key')}",
            f"- Concept: {_gap_concept(a)}",
            f"- Selected: {selected}",
            f"- Correct: {correct}",
            f"- Confidence: {a.get('confidence') or 'not recorded'}",
            "",
        ]
    prompt = continuation_prompt(data)
    md += [
        "## Compact ChatGPT continuation prompt", "", "```text", prompt,
        "```", "",
    ]

    base = f"session-{session_id}"
    report_path = out / f"{base}-report.md"
    prompt_path = out / f"{base}-prompt.txt"
    context_path = out / f"{base}-context.json"
    report_path.write_text("\n".join(md), encoding="utf-8")
    prompt_path.write_text(prompt + "\n", encoding="utf-8")

    context = {
        "schema_version": 1,
        "purpose": (
            "Minimal session context pack; "
            "local question bank remains authoritative."
        ),
        "session": s,
        "continuation_prompt": prompt,
        "questions": bundle.questions,
    }
    context_path.write_text(json.dumps(context, indent=2), encoding="utf-8")
    return {
        "report": report_path, "prompt": prompt_path, "context": context_path,
    }
