"""Shared quiz choice parsing and terminal question/result rendering."""

from __future__ import annotations

from .quiz_models import AnswerResult, Option, Question
from .terminal import print_wrapped


def parse_answer(raw: str, labels: list[str]) -> set[int]:
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


def option_texts(options: tuple[Option, ...]) -> list[str]:
    """Format choices using their saved labels and exact answer text."""
    return [f"{option.label}. {option.text}" for option in options]


def display_question(
    question: Question,
    position: int,
    total: int,
    width: int,
    *,
    selected: frozenset[int] = frozenset(),
) -> None:
    """Show saved choices and selected IDs without revealing grading."""
    prefix = f"[{position}/{total}] "
    print()
    print_wrapped(
        question.text,
        preferred_width=width,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
    )
    print()
    for option in question.options:
        chosen = option.id in selected
        prefix = f"{'>' if chosen else ' '} {option.label}. "
        print_wrapped(
            option.text,
            preferred_width=width,
            initial_indent=prefix,
            subsequent_indent=" " * len(prefix),
            bold=chosen,
        )
    if question.kind == "multi_select":
        print_wrapped(
            f"(Select exactly {question.select_count} answers; enter uppercase or lowercase "
            "letters separated by spaces, commas, or semicolons.)",
            preferred_width=width,
            initial_indent="  ",
            subsequent_indent="  ",
        )


def print_results(
    results: tuple[AnswerResult, ...], width: int, *, review: bool
) -> None:
    """Show finalized scores and, on request, selected/correct missed choices."""
    correct_n = sum(result.is_correct for result in results)
    score = 100 * correct_n / len(results) if results else 0.0
    print()
    print_wrapped(
        f"Score: {correct_n}/{len(results)} ({score:.1f}%)",
        preferred_width=width,
    )
    if review:
        print("\nReview:")
        for index, result in enumerate(results, 1):
            if not result.is_correct:
                selected = "; ".join(option_texts(result.selected_options))
                correct = "; ".join(option_texts(result.correct_options))
                print_wrapped(
                    f"Q{index}: ✗ selected {selected} | correct {correct}",
                    preferred_width=width,
                    initial_indent="  ",
                    subsequent_indent="      ",
                )
