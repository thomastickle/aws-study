"""Study-mode terminal interaction and new-quiz dispatch."""

from __future__ import annotations

import time

from .exam_ui import run_exam
from .quiz_rendering import (
    display_question,
    option_texts,
    parse_answer,
    print_results,
)
from .quiz_service import QuizService
from .terminal import DEFAULT_WIDTH, print_wrapped, terminal_width, wrap_text


def _prompt_answer(
    labels: list[str],
    *,
    select_count: int = 1,
    width: int = DEFAULT_WIDTH,
) -> set[int]:
    while True:
        try:
            selected = parse_answer(input("Answer: "), labels)
            if len(selected) != select_count:
                raise ValueError(
                    f"This question requires exactly {select_count} selections; "
                    f"you entered {len(selected)}."
                )
            return selected
        except ValueError as e:
            print_wrapped(str(e), preferred_width=width)


def _prompt_confidence(*, width: int = DEFAULT_WIDTH) -> str | None:
    prompt = wrap_text(
        "Confidence [(C)onfident, (E)ducated Guess, (U)nsure, Enter to skip]:",
        width=max(1, terminal_width(width) - 1),
    )
    raw = input(prompt + " ").strip().lower()
    return {
        "c": "high",
        "confident": "high",
        "e": "medium",
        "educated guess": "medium",
        "u": "low",
        "unsure": "low",
    }.get(raw)


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
    width: int = DEFAULT_WIDTH,
) -> int:
    """Run terminal prompts and feedback using the shared quiz service."""
    if width < 1:
        raise ValueError("Wrap width must be at least 1.")
    session_id = service.create_session(
        cert_id,
        count=count,
        target_year=target_year,
        mode=mode,
        strategy=strategy,
        seed=seed,
        include_unverified=include_unverified,
    )
    if mode == "exam":
        return run_exam(service, session_id, width=width)
    return _run_study(service, session_id, width=width)


def _run_study(service: QuizService, session_id: int, *, width: int) -> int:
    questions = service.questions(session_id)
    results = []
    for position, question in enumerate(questions, 1):
        display_question(question, position, len(questions), width)
        start = time.monotonic()
        selected = _prompt_answer(
            [o.label for o in question.options],
            select_count=question.select_count,
            width=width,
        )
        elapsed = int((time.monotonic() - start) * 1000)
        confidence = _prompt_confidence(width=width)
        result = service.record_answer(
            session_id,
            question.id,
            {question.options[index].id for index in selected},
            confidence,
            elapsed,
        )
        results.append(result)
        print_wrapped(
            "✓ Correct" if result.is_correct else "✗ Incorrect",
            preferred_width=width,
        )
        print_wrapped(
            "Correct answer(s): "
            + "; ".join(option_texts(result.correct_options)),
            preferred_width=width,
            subsequent_indent="  ",
        )
        for option in question.options:
            if option.rationale:
                prefix = f"  {option.label} rationale: "
                print_wrapped(
                    option.rationale,
                    preferred_width=width,
                    initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
    service.finish_session(session_id)
    print_results(tuple(results), width, review=False)
    return session_id
