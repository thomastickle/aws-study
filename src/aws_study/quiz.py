from __future__ import annotations

import time

from .quiz_models import AnswerResult, Option, Question
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
    select_count: int = 1,
    width: int = DEFAULT_WIDTH,
) -> set[int]:
    while True:
        try:
            selected = _parse_answer(input("Answer: "), labels)
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
    if mode == "exam":
        return run_exam(service, session_id, width=width)
    return _run_study(service, session_id, width=width)


def _display_question(
    question: Question,
    position: int,
    total: int,
    width: int,
    *,
    selected: frozenset[int] = frozenset(),
) -> None:
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


def _print_results(
    results: tuple[AnswerResult, ...], width: int, *, review: bool
) -> None:
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
                selected = "; ".join(_option_texts(result.selected_options))
                correct = "; ".join(_option_texts(result.correct_options))
                print_wrapped(
                    f"Q{index}: ✗ selected {selected} | correct {correct}",
                    preferred_width=width,
                    initial_indent="  ",
                    subsequent_indent="      ",
                )


def _run_study(service: QuizService, session_id: int, *, width: int) -> int:
    questions = service.questions(session_id)
    results = []
    for position, question in enumerate(questions, 1):
        _display_question(question, position, len(questions), width)
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
    _print_results(tuple(results), width, review=False)
    return session_id


def choose_saved_exam(
    service: QuizService, cert_id: int, *, width: int
) -> int | None:
    """Offer existing drafts; return zero for quit and None for a fresh exam."""
    saved = service.saved_exams(cert_id)
    if not saved:
        return None
    print("\nSaved draft exams:")
    for exam in saved:
        print_wrapped(
            f"Session {exam.session_id}: {exam.answered}/{exam.total} answered, "
            f"{exam.flagged} flagged; started {exam.started_at or 'unknown'}",
            preferred_width=width,
            initial_indent="  ",
            subsequent_indent="  ",
        )
    ids = {exam.session_id for exam in saved}
    while True:
        try:
            raw = (
                input("Resume session ID, N=new exam, Q=quit: ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            return 0
        if raw in ("n", "new"):
            return None
        if raw in ("q", "quit"):
            return 0
        if raw.isdigit() and int(raw) in ids:
            return int(raw)
        print_wrapped(
            "Choose a listed session ID, N, or Q.", preferred_width=width
        )


def _question_command(raw: str) -> str | None:
    if not raw.startswith(":"):
        return None
    aliases = {
        "f": "flag",
        "flag": "flag",
        "n": "next",
        "next": "next",
        "p": "previous",
        "previous": "previous",
        "r": "review",
        "review": "review",
        "q": "quit",
        "quit": "quit",
    }
    command = aliases.get(raw[1:].strip().lower())
    if command is None:
        raise ValueError("Unknown command. Use :f, :n, :p, :r, or :q.")
    return command


def _edit_exam_question(
    service: QuizService, session_id: int, index: int, width: int
) -> str:
    """Edit one draft and return a navigation action, checkpointing active time."""
    items = service.review_items(session_id)
    item = items[index]
    question = item.question
    _display_question(
        question,
        item.number,
        len(items),
        width,
        selected=item.response.selected,
    )
    print_wrapped(
        "Current answer: "
        + (",".join(item.selected_labels) or "unanswered")
        + f"; confidence: {item.response.confidence or 'not recorded'}; "
        + f"flagged: {'yes' if item.response.flagged else 'no'}",
        preferred_width=width,
    )
    print_wrapped(
        "Selected answers are marked >. Commands: :f=flag, :n=next/skip, "
        ":p=previous, :r=review index, :q=quit.",
        preferred_width=width,
    )
    checkpoint = time.monotonic()

    def save_time() -> None:
        nonlocal checkpoint
        now = time.monotonic()
        service.add_elapsed(
            session_id, question.id, max(0, int((now - checkpoint) * 1000))
        )
        checkpoint = now

    def dispatch(command: str) -> str | None:
        save_time()
        if command == "flag":
            service.toggle_flag(session_id, question.id)
            flagged = service.review_items(session_id)[index].response.flagged
            print_wrapped(
                f"Flagged: {'yes' if flagged else 'no'}", preferred_width=width
            )
            return None
        return command

    try:
        while True:
            try:
                raw = input("Answer [Enter=keep current]: ").strip()
                command = _question_command(raw)
                if command:
                    action = dispatch(command)
                    if action:
                        return action
                    continue
                current = service.review_items(session_id)[index].response
                selected = (
                    set(current.selected)
                    if not raw
                    else {
                        question.options[i].id
                        for i in _parse_answer(
                            raw, [o.label for o in question.options]
                        )
                    }
                )
                service.validate_selection(question, selected)
                service.save_response(session_id, question.id, selected)
                save_time()
                break
            except ValueError as error:
                print_wrapped(str(error), preferred_width=width)
        while True:
            try:
                confidence_prompt = wrap_text(
                    "Confidence [(C)onfident, (E)ducated Guess, (U)nsure, "
                    "Enter=keep current, none=clear]:",
                    width=max(1, terminal_width(width) - 1),
                )
                raw = input(confidence_prompt + " ").strip().lower()
                command = _question_command(raw)
                if command:
                    action = dispatch(command)
                    if action:
                        return action
                    continue
                choices = {
                    "c": "high",
                    "confident": "high",
                    "e": "medium",
                    "educated guess": "medium",
                    "u": "low",
                    "unsure": "low",
                    "none": None,
                }
                if raw:
                    if raw not in choices:
                        raise ValueError("Choose C, E, U, none, or Enter.")
                    service.set_confidence(
                        session_id, question.id, choices[raw]
                    )
                save_time()
                return "answered"
            except ValueError as error:
                print_wrapped(str(error), preferred_width=width)
    except (EOFError, KeyboardInterrupt):
        save_time()
        return "quit"


def _review_exam(
    service: QuizService, session_id: int, width: int
) -> int | None:
    """Return a question index to edit, or None on quit/final submission."""
    while True:
        items = service.review_items(session_id)
        print("\nReview before submitting\n")
        print_wrapped(
            "* = flagged or incomplete; choose a number to view the full "
            "question and your selected answers.",
            preferred_width=width,
        )
        for item in items:
            marks = " [FLAGGED]" if item.response.flagged else ""
            if item.status != "ANSWERED":
                marks += f" [{item.status}]"
            attention = "*" if marks else " "
            print_wrapped(
                f"{attention} {item.number:>3}. "
                f"{','.join(item.selected_labels) or '-'}{marks}",
                preferred_width=width,
            )
        print_wrapped(
            "Commands: <n> or R <n>=revisit, F <n>=toggle flag, "
            "S=submit, Q=quit and resume later.",
            preferred_width=width,
        )
        try:
            tokens = input("Review: ").strip().lower().split()
            if not tokens:
                continue
            if len(tokens) == 1 and tokens[0].isdigit():
                tokens = ["r", tokens[0]]
            if tokens[0] in ("r", "f") and len(tokens) == 2:
                number = int(tokens[1])
                if not 1 <= number <= len(items):
                    raise ValueError("Choose a listed question number.")
                if tokens[0] == "r":
                    return number - 1
                service.toggle_flag(session_id, items[number - 1].question.id)
            elif tokens == ["q"]:
                return None
            elif tokens == ["s"]:
                errors = service.submission_errors(session_id)
                if errors:
                    print_wrapped(
                        "Cannot submit.\n" + "\n".join(errors),
                        preferred_width=width,
                    )
                    continue
                print_wrapped(
                    f"{len(items)}/{len(items)} answered; "
                    f"{sum(i.response.flagged for i in items)} flagged.",
                    preferred_width=width,
                )
                if input("Submit exam? [y/N]: ").strip().lower() not in (
                    "y",
                    "yes",
                ):
                    continue
                results = service.submit_session(session_id)
                _print_results(results, width, review=True)
                return None
            else:
                raise ValueError(
                    "Use a question number, R <n>, F <n>, S, or Q."
                )
        except ValueError as error:
            print_wrapped(str(error), preferred_width=width)
        except (EOFError, KeyboardInterrupt):
            return None


def run_exam(
    service: QuizService, session_id: int, *, width: int = DEFAULT_WIDTH
) -> int:
    """Resume the saved exam through question, review, and submission phases."""
    if width < 1:
        raise ValueError("Wrap width must be at least 1.")
    items = service.resume_session(session_id)
    index = next(
        (i for i, item in enumerate(items) if item.status != "ANSWERED"), None
    )
    editing_from_review = False
    while True:
        if index is None:
            index = _review_exam(service, session_id, width)
            if index is None:
                break
            editing_from_review = True
        action = _edit_exam_question(service, session_id, index, width)
        if action == "quit":
            break
        if action == "review" or (
            action == "answered" and editing_from_review
        ):
            index = None
        elif action == "previous":
            index = max(0, index - 1)
        else:
            index = index + 1 if index + 1 < len(items) else None
    if not service.is_complete(session_id):
        print_wrapped(
            f"Draft exam {session_id} saved. Resume with: "
            f"python aws-study.py quiz --resume {session_id}",
            preferred_width=width,
        )
    return session_id
