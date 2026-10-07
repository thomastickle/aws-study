# Project guidance

This project is an application for studying and practicing for AWS certification
exams. The current question bank focuses on AWS Certified Cloud Practitioner.

## Requirements

- Support the Python version declared in `pyproject.toml`, currently Python 3.12+.
  Keep code compatible with that minimum unless a newer feature is needed and
  the supported version is deliberately raised.
- Follow PEP 8 for new and modified Python code. Keep formatting changes focused
  on the code being changed.
- Use descriptive names and type hints for public interfaces. Document expected
  types when data structures have a non-obvious shape.
- Document public interfaces and non-obvious behavior. Comments should explain
  design decisions, constraints, and surprising behavior.
- Keep question banks, raw assessment exports, databases, personal attempt
  history, and generated reports out of Git. Preserve the existing ignore rules;
  `private/README.md` is the documented exception within `private/`.
- Tests must use synthetic study data rather than private question banks or
  personal attempt history.
- Add or update tests for behavior changes that need regression coverage. Run
  the relevant checks before completing a change and report any checks that
  could not be run.

## Design preferences

- Favor small, cohesive functions and classes. Prefer simplicity and readability.
- Use modules and functions when they provide sufficient structure. Introduce
  classes when they help manage state or model behavior.
- Separate business rules from user-interface code and persistence where
  practical. Introduce service or repository abstractions when they clarify
  responsibilities or reduce duplication.
- Group related functionality into cohesive modules. Introduce subpackages when
  several related modules justify the additional structure.

## Database migrations

- Every database schema change must include migration code in the same change.
  Increment `SCHEMA_VERSION`, add and register a new versioned migration, and
  update schema detection, documentation, and tests. Do not rewrite migration
  scripts already applied to released databases.
- Preserve an upgrade path from every supported older database version to the
  current version. Apply versioned SQL upgrades in ascending order; legacy
  structural conversions must explicitly map content and history.
- Upgrades must be transactional and repeated initialization must be harmless.
  Preserve question identity, provenance, sessions, attempts, selected answers,
  confidence, timing, review notes, and saved answer order. Reject incompatible
  records or unsupported/newer versions rather than discarding history.
- Test each supported starting version with synthetic data, including failure
  rollback, foreign-key/integrity checks, and history/answer-order preservation.
  `migrate-db` must preserve its source, refuse an existing destination, and
  publish the upgraded copy only after validation succeeds.
- SQLite schema versions and question-bank JSON schema versions are independent.
  Changing a bank format requires an explicit conversion/adoption path and tests;
  do not mix personal attempt history back into question-bank files or infer
  authoritative classification during a format conversion.

## Verification

Run commands from the repository root using the project virtual environment.
The test runner needs `src/` on the Python import path.

On Linux/macOS:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
PYTHONPATH=src .venv/bin/python -m coverage run -m unittest discover -s tests -v
.venv/bin/python -m coverage report
PYTHONPATH=src .venv/bin/python -O -m unittest discover -s tests -q
.venv/bin/python scripts/check_repo_safety.py
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m coverage run -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m coverage report
.\.venv\Scripts\python.exe -O -m unittest discover -s tests -q
.\.venv\Scripts\python.exe scripts/check_repo_safety.py
```

Run the repository safety check before preparing a commit for publication.
Install development tools with `uv sync --extra dev` or
`python -m pip install -e '.[dev]'` in the project virtual environment.
Keep runtime dependencies empty.
The coverage gate is 90%, based on the measured pre-cleanup 93% branch coverage.
