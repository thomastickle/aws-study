import os
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from aws_study.cli import build_parser
from aws_study.terminal import print_wrapped, terminal_width, wrap_text


class TerminalTests(unittest.TestCase):
    def test_width_caps_wide_terminals_and_keeps_a_right_margin(self):
        for columns, expected in ((126, 80), (80, 78), (52, 50), (2, 1)):
            with self.subTest(columns=columns), patch(
                "aws_study.terminal.shutil.get_terminal_size",
                return_value=os.terminal_size((columns, 24)),
            ):
                self.assertEqual(terminal_width(), expected)
        with patch("aws_study.terminal.shutil.get_terminal_size",
                   return_value=os.terminal_size((126, 24))):
            self.assertEqual(terminal_width(100), 100)
            self.assertEqual(terminal_width(126), 124)

    def test_missing_terminal_uses_preferred_width(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("aws_study.terminal.shutil.os.get_terminal_size",
                  side_effect=OSError("No terminal")),
        ):
            self.assertEqual(terminal_width(), 80)
            self.assertEqual(terminal_width(100), 100)
        with self.assertRaisesRegex(ValueError, "at least 1"):
            terminal_width(0)

    def test_question_and_option_hanging_indents_preserve_words(self):
        text = (
            "Choose the suitable service for this synthetic example "
            "while considering each available answer carefully."
        )
        for prefix in ("[1/20] ", "  A. "):
            with self.subTest(prefix=prefix):
                wrapped = wrap_text(
                    text, width=40, initial_indent=prefix,
                    subsequent_indent=" " * len(prefix),
                )
                lines = wrapped.splitlines()
                self.assertGreater(len(lines), 1)
                self.assertTrue(lines[0].startswith(prefix))
                self.assertTrue(all(line.startswith(" " * len(prefix))
                                    for line in lines[1:]))
                self.assertTrue(all(len(line) <= 40 for line in lines))
                self.assertEqual(
                    " ".join(line[len(prefix):] for line in lines), text,
                )

    def test_explicit_paragraphs_and_newlines_are_preserved(self):
        text = "First paragraph with several words.\n\nSecond paragraph.\n"
        wrapped = wrap_text(text, width=25)
        self.assertIn("\n\nSecond paragraph.\n", wrapped)
        self.assertEqual(wrapped.split(), text.split())

    def test_hyphenated_words_and_long_tokens_are_kept_intact(self):
        text = "Use availability-zone settings abcdefghijklmnopqrstuvwxyz now"
        wrapped = wrap_text(text, width=20)
        self.assertEqual(wrapped.split(), text.split())
        self.assertIn("availability-zone", wrapped)
        self.assertIn("abcdefghijklmnopqrstuvwxyz", wrapped)

    def test_empty_opening_paragraph_keeps_question_number(self):
        wrapped = wrap_text(
            "\nQuestion text", width=40, initial_indent="[1/20] ",
            subsequent_indent="       ",
        )
        self.assertEqual(wrapped, "[1/20]\n       Question text")

    def test_printing_adapts_after_terminal_resize(self):
        text = "Separate words in a sentence that should wrap when space is small"
        with patch("aws_study.terminal.shutil.get_terminal_size", side_effect=[
            os.terminal_size((32, 24)), os.terminal_size((126, 24)),
        ]):
            with redirect_stdout(StringIO()) as narrow:
                print_wrapped(text)
            with redirect_stdout(StringIO()) as wide:
                print_wrapped(text)
        self.assertGreater(len(narrow.getvalue().splitlines()), 1)
        self.assertTrue(all(len(line) <= 30
                            for line in narrow.getvalue().splitlines()))
        self.assertEqual(wide.getvalue(), text + "\n")

    def test_cli_width_defaults_and_override(self):
        parser = build_parser()
        self.assertEqual(parser.parse_args([
            "quiz", "--cert", "TEST-C01",
        ]).width, 80)
        self.assertEqual(parser.parse_args([
            "quiz", "--cert", "TEST-C01", "--width", "100",
        ]).width, 100)


if __name__ == "__main__":
    unittest.main()
