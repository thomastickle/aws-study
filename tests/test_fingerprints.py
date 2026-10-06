import unittest

from aws_study.fingerprints import (
    FINGERPRINT_VERSION,
    answer_key_fingerprint,
    content_fingerprint,
    normalize_match_text,
)


class FingerprintTests(unittest.TestCase):
    def test_conservative_normalization(self):
        self.assertEqual(
            normalize_match_text("  ＡWS\u00a0  Straße\n"), "aws strasse"
        )
        self.assertNotEqual(
            normalize_match_text("AWS?"), normalize_match_text("AWS")
        )

    def test_identity_ignores_case_whitespace_order_and_unicode_width(self):
        expected = content_fingerprint(
            "AWS question?", ["Choice A", "Choice B"]
        )
        for stem, answers in [
            (" aws QUESTION? ", ["choice b", " choice   a "]),
            ("AWS\u00a0question?", ["Choice\nA", "Choice B"]),
            ("ＡWS question?", ["Choice A", "Choice B"]),
        ]:
            self.assertEqual(content_fingerprint(stem, answers), expected)

    def test_content_and_answer_key_are_independent(self):
        original = content_fingerprint("Question?", ["A", "B"])
        self.assertNotEqual(
            original, content_fingerprint("Question!", ["A", "B"])
        )
        self.assertNotEqual(
            original, content_fingerprint("Question?", ["A", "C"])
        )
        self.assertNotEqual(
            answer_key_fingerprint(["A"]), answer_key_fingerprint(["B"])
        )
        self.assertEqual(
            answer_key_fingerprint(["B", "A"]),
            answer_key_fingerprint([" a ", "b"]),
        )
        self.assertEqual(FINGERPRINT_VERSION, 1)
