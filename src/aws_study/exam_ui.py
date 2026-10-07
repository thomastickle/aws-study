"""Terminal navigation for durable exam drafts, review, and submission."""

from __future__ import annotations

import time

from .quiz_rendering import display_question, parse_answer, print_results
from .quiz_service import QuizService
from .terminal import DEFAULT_WIDTH, print_wrapped, terminal_width, wrap_text


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
    service: QuizService,
    session_id: int,
    question_id: int,
    total: int,
    width: int,
) -> str:
    """Edit one draft and return a navigation action, checkpointing active time."""
    item = service.review_item(session_id, question_id)
    question = item.question
    display_question(
        question,
        item.number,
        total,
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

    def elapsed_interval() -> int:
        return max(0, int((time.monotonic() - checkpoint) * 1000))

    def save_time() -> None:
        nonlocal checkpoint
        service.add_elapsed(session_id, question.id, elapsed_interval())
        checkpoint = time.monotonic()

    def dispatch(command: str) -> str | None:
        nonlocal checkpoint
        save_time()
        if command == "flag":
            service.toggle_flag(session_id, question.id)
            flagged = service.review_item(
                session_id, question_id
            ).response.flagged
            print_wrapped(
                f"Flagged: {'yes' if flagged else 'no'}", preferred_width=width
            )
            checkpoint = time.monotonic()
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
                current = service.review_item(session_id, question_id).response
                selected = (
                    set(current.selected)
                    if not raw
                    else {
                        question.options[i].id
                        for i in parse_answer(
                            raw, [o.label for o in question.options]
                        )
                    }
                )
                service.validate_selection(question, selected)
                service.save_response(
                    session_id,
                    question.id,
                    selected,
                    elapsed_ms=elapsed_interval(),
                )
                checkpoint = time.monotonic()
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
                if raw and raw not in choices:
                    raise ValueError("Choose C, E, U, none, or Enter.")
                elapsed_ms = elapsed_interval()
                if raw:
                    service.set_confidence(
                        session_id, question.id, choices[raw]
                    )
                service.add_elapsed(session_id, question.id, elapsed_ms)
                checkpoint = time.monotonic()
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
                print_results(results, width, review=True)
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
        action = _edit_exam_question(
            service, session_id, items[index].question.id, len(items), width
        )
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
