"""The publication guard rejects private files and fails closed."""

import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.check_repo_safety import main, sensitive_paths


class SafetyTests(unittest.TestCase):
    def test_public_files_and_readme_exception(self):
        self.assertEqual(
            sensitive_paths(
                [
                    "private/README.md",
                    "README.md",
                    "src/aws_study/db.py",
                    "examples/question-bank.sample.json",
                    "tests/fixtures/schema_v1.sql",
                ]
            ),
            [],
        )

    def test_all_private_directories_and_suffixes(self):
        paths = [
            "private/bank.json",
            "private/nested/README.md",
            "question-banks/bank.json",
            "imports/bank.json",
            "reports/output.txt",
            "nested/reports/output.md",
            *[
                f"nested/study{suffix}"
                for suffix in (
                    ".db",
                    ".db-shm",
                    ".db-wal",
                    ".sqlite",
                    ".sqlite3",
                    ".html",
                    ".htm",
                    ".mhtml",
                    ".DB",
                    ".HTM",
                )
            ],
        ]
        self.assertEqual(sensitive_paths(paths), paths)

    def test_nul_delimited_paths_include_newlines_and_spaces(self):
        stdout, stderr = StringIO(), StringIO()
        with (
            patch(
                "scripts.check_repo_safety.subprocess.check_output",
                return_value="private/README.md\0reports/one\ntwo.txt\0my study.db\0",
            ) as git,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(main(), 1)
        self.assertEqual(git.call_args.args[0], ["git", "ls-files", "-z"])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("reports/one\\ntwo.txt", stderr.getvalue())
        self.assertIn("my study.db", stderr.getvalue())

    def test_git_failures_are_errors(self):
        for failure in (
            FileNotFoundError("git missing"),
            PermissionError("denied"),
            subprocess.CalledProcessError(128, ["git", "ls-files"]),
        ):
            with self.subTest(failure=type(failure).__name__):
                stdout, stderr = StringIO(), StringIO()
                with (
                    patch(
                        "scripts.check_repo_safety.subprocess.check_output",
                        side_effect=failure,
                    ),
                    redirect_stdout(stdout),
                    redirect_stderr(stderr),
                ):
                    self.assertEqual(main(), 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn("could not run", stderr.getvalue())

    def test_clean_repository_succeeds(self):
        with (
            patch(
                "scripts.check_repo_safety.subprocess.check_output",
                return_value="README.md\0private/README.md\0",
            ),
            redirect_stdout(StringIO()),
        ):
            self.assertEqual(main(), 0)

    def test_real_git_index_and_nonrepository(self):
        script = (
            Path(__file__).resolve().parents[1]
            / "scripts/check_repo_safety.py"
        )
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)

            def run():
                return subprocess.run(
                    [sys.executable, str(script)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )

            self.assertEqual(run().returncode, 2)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "private").mkdir()
            (root / "private/README.md").write_text("Public placeholder")
            subprocess.run(
                ["git", "add", "private/README.md"], cwd=root, check=True
            )
            self.assertEqual(run().returncode, 0)
            (root / "private/study.db").write_text("Synthetic file")
            subprocess.run(
                ["git", "add", "private/study.db"], cwd=root, check=True
            )
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn("private/study.db", result.stderr)
