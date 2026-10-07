"""Reject tracked private study data, including files ignored by Git."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

SENSITIVE_DIRECTORIES = {"question-banks", "imports", "reports"}
SENSITIVE_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".html",
    ".htm",
    ".mhtml",
)


def sensitive_paths(paths: list[str]) -> list[str]:
    """Match repository ignore rules; only private/README.md is public."""
    return [
        path
        for path in paths
        if path != "private/README.md"
        and (
            path.startswith("private/")
            or SENSITIVE_DIRECTORIES.intersection(
                PurePosixPath(path).parts[:-1]
            )
            or path.lower().endswith(SENSITIVE_SUFFIXES)
        )
    ]


def main() -> int:
    """Return failure if private files are tracked or Git cannot be inspected."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"],
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, OSError) as error:
        print(
            f"Repository safety check could not run: {error}", file=sys.stderr
        )
        return 2
    bad = sensitive_paths([path for path in out.split("\0") if path])
    if bad:
        print(
            "Refusing: potentially private study data is tracked by Git:",
            file=sys.stderr,
        )
        for path in bad:
            print(f"  {path!r}", file=sys.stderr)
        print(
            "Remove it from the index before publishing, e.g. git rm --cached <file>.",
            file=sys.stderr,
        )
        return 1
    print(
        "Repository safety check passed: no obvious private bank/database files are tracked."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
