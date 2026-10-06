"""Word wrapping for plain terminal output."""
from __future__ import annotations

import shutil
import textwrap

DEFAULT_WIDTH = 80
RIGHT_MARGIN = 2


def terminal_width(preferred: int = DEFAULT_WIDTH) -> int:
    """Cap text at the preferred width, leaving room at the terminal edge.

    When terminal size is unavailable, use the preferred width. Unusually
    narrow terminals still receive a positive wrapping width.
    """
    if preferred < 1:
        raise ValueError("Wrap width must be at least 1.")
    columns = shutil.get_terminal_size(
        fallback=(preferred + RIGHT_MARGIN, 24),
    ).columns
    return min(preferred, max(1, columns - RIGHT_MARGIN))


def wrap_text(
    text: str,
    *,
    width: int,
    initial_indent: str = "",
    subsequent_indent: str = "",
) -> str:
    """Wrap at whitespace, preserving paragraphs and whole hyphenated words.

    Width includes indentation. A token longer than the available line is
    kept intact rather than truncated or split. Explicit newlines remain;
    continuation lines use the supplied hanging indent.
    """
    wrapper = textwrap.TextWrapper(
        width=width, initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
        break_long_words=False, break_on_hyphens=False,
    )
    lines = []
    for index, paragraph in enumerate(text.split("\n")):
        if index:
            wrapper.initial_indent = subsequent_indent
        if paragraph.strip():
            lines.append(wrapper.fill(paragraph))
        else:
            # An empty opening paragraph must not swallow a question number
            # or answer label supplied as its prefix.
            lines.append(initial_indent.rstrip() if index == 0 else "")
    return "\n".join(lines)


def print_wrapped(
    text: str,
    *,
    preferred_width: int = DEFAULT_WIDTH,
    initial_indent: str = "",
    subsequent_indent: str = "",
) -> None:
    """Print using the current terminal size, including after a resize."""
    print(wrap_text(
        text, width=terminal_width(preferred_width),
        initial_indent=initial_indent, subsequent_indent=subsequent_indent,
    ))
