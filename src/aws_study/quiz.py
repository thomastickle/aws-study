from __future__ import annotations

import time

from .quiz_models import AnswerResult, Option
from .quiz_service import QuizService


def _parse_answer(raw: str, labels: list[str]) -> set[int]:
    """Parse choices separated by whitespace, commas, or semicolons."""
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


def _option_texts(options: tuple[Option, ...]) -> list[str]:
    return [f"{option.label}. {option.text}" for option in options]


def run_quiz(
    service: QuizService,
    cert_id: int,
    *,
    count: int,
    target_year: int | None,
    mode: str,
    strategy: str,
    seed: int | None,
    include_unverified: bool = False,
) -> int:
    """Run terminal prompts and feedback using the shared quiz service."""
    session_id = service.create_session(
        cert_id, count=count, target_year=target_year, mode=mode,
        strategy=strategy, seed=seed, include_unverified=include_unverified,
    )
    questions = service.questions(session_id)
    pending_results: list[AnswerResult] = []
    for position, question in enumerate(questions, 1):
        labels = [option.label for option in question.options]
        print(f"\n[{position}/{len(questions)}] {question.text}\n")
        for option in question.options:
            print(f"  {option.label}. {option.text}")
        if question.kind == "multi_select":
            print(
                "  (Select all that apply; enter uppercase or lowercase "
                "letters separated by spaces, commas, or semicolons.)"
            )

        start = time.monotonic()
        selected = _prompt_answer(labels)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        confidence = _prompt_confidence()
        result = service.record_answer(
            session_id, question.id,
            {question.options[index].id for index in selected},
            confidence, elapsed_ms,
        )
        pending_results.append(result)

        if mode == "study":
            print("✓ Correct" if result.is_correct else "✗ Incorrect")
            print(
                "Correct answer(s): "
                + "; ".join(_option_texts(result.correct_options))
            )
            for option in question.options:
                if option.rationale:
                    print(f"  {option.label} rationale: {option.rationale}")

    service.finish_session(session_id)
    correct_n = sum(result.is_correct for result in pending_results)
    score = 100 * correct_n / len(pending_results)
    print(f"\nScore: {correct_n}/{len(pending_results)} ({score:.1f}%)")
    if mode == "exam":
        print("\nReview:")
        for index, result in enumerate(pending_results, 1):
            if not result.is_correct:
                selected_text = "; ".join(
                    _option_texts(result.selected_options)
                )
                correct_text = "; ".join(_option_texts(result.correct_options))
                print(
                    f"  Q{index}: ✗ selected {selected_text} "
                    f"| correct {correct_text}"
                )
    return session_id
