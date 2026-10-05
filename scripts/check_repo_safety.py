from __future__ import annotations

import subprocess
import sys

SENSITIVE_PREFIXES = ("private/", "question-banks/", "imports/")
SENSITIVE_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".html", ".mhtml")


def main() -> int:
    try:
        out = subprocess.check_output(["git", "ls-files"], text=True)
    except Exception:
        print("Not a Git repository or git unavailable; nothing to check.")
        return 0
    bad = []
    for f in out.splitlines():
        if f == "private/README.md":
            continue
        if f.startswith(SENSITIVE_PREFIXES) or f.lower().endswith(SENSITIVE_SUFFIXES):
            bad.append(f)
    if bad:
        print("Refusing: potentially private study data is tracked by Git:")
        for f in bad:
            print(f"  {f}")
        print("Remove it from the index before publishing, e.g. git rm --cached <file>.")
        return 1
    print("Repository safety check passed: no obvious private bank/database files are tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
