from __future__ import annotations

import time

from .quiz_models import AnswerResult, Option
from .quiz_service import QuizService
from .terminal import DEFAULT_WIDTH, print_wrapped, terminal_width, wrap_text


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


def _prompt_answer(
    labels: list[str],
    *,
    width: int = DEFAULT_WIDTH,
) -> set[int]:
    while True:
        try:
            selected = _parse_answer(input("Answer: "), labels)
            if not selected:
                raise ValueError("Choose at least one option.")
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
    questions = service.questions(session_id)
    pending_results: list[AnswerResult] = []
    for position, question in enumerate(questions, 1):
        labels = [option.label for option in question.options]
        prefix = f"[{position}/{len(questions)}] "
        print()
        print_wrapped(
            question.text,
            preferred_width=width,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
        )
        print()
        for option in question.options:
            prefix = f"  {option.label}. "
            print_wrapped(
                option.text,
                preferred_width=width,
                initial_indent=prefix,
                subsequent_indent=" " * len(prefix),
            )
        if question.kind == "multi_select":
            print_wrapped(
                f"(Select {question.select_count} answers; enter uppercase or lowercase "
                "letters separated by spaces, commas, or semicolons.)",
                preferred_width=width,
                initial_indent="  ",
                subsequent_indent="  ",
            )

        start = time.monotonic()
        selected = _prompt_answer(labels, width=width)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        confidence = _prompt_confidence(width=width)
        result = service.record_answer(
            session_id,
            question.id,
            {question.options[index].id for index in selected},
            confidence,
            elapsed_ms,
        )
        pending_results.append(result)

        if mode == "study":
            print_wrapped(
                "✓ Correct" if result.is_correct else "✗ Incorrect",
                preferred_width=width,
            )
            print_wrapped(
                "Correct answer(s): "
                + "; ".join(_option_texts(result.correct_options)),
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
    correct_n = sum(result.is_correct for result in pending_results)
    score = 100 * correct_n / len(pending_results)
    print()
    print_wrapped(
        f"Score: {correct_n}/{len(pending_results)} ({score:.1f}%)",
        preferred_width=width,
    )
    if mode == "exam":
        print("\nReview:")
        for index, result in enumerate(pending_results, 1):
            if not result.is_correct:
                selected_text = "; ".join(
                    _option_texts(result.selected_options)
                )
                correct_text = "; ".join(_option_texts(result.correct_options))
                print_wrapped(
                    f"Q{index}: ✗ selected {selected_text} "
                    f"| correct {correct_text}",
                    preferred_width=width,
                    initial_indent="  ",
                    subsequent_indent="      ",
                )
    return session_id
