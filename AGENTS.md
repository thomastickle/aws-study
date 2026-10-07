# Repository Guidelines

This project is an application for studying and practicing for AWS certification
exams. The current question bank focuses on AWS Certified Cloud Practitioner.

## Project structure

- `src/aws_study/` contains the CLI, quiz logic, reporting, repositories, models,
  and versioned SQLite schema scripts.
- `tests/` contains automated tests; `tests/fixtures/` holds synthetic database
  fixtures, and `tests/study_fixture.py` supplies shared synthetic bank data.
- `examples/` contains the public question-bank sample and JSON schema.
- `scripts/` contains repository maintenance checks.
- `private/` contains local banks, databases, attempt history, and reports.
- `.github/workflows/ci.yml` runs quality checks, cross-platform tests, and builds.

## Coding style and naming

- Support the Python version declared in `pyproject.toml`, currently Python 3.12+.
  Keep code compatible with that minimum unless a newer feature is needed and
  the supported version is deliberately raised.
- Follow PEP 8: four-space indentation, `snake_case` functions and modules,
  `PascalCase` classes, and `UPPER_SNAKE_CASE` constants. Ruff enforces formatting
  with a 79-column limit. Keep formatting changes focused on modified code.
- Use descriptive names and type hints for public interfaces. Document expected
  types when data structures have a non-obvious shape.
- Document public interfaces and non-obvious behavior. Comments should explain
  design decisions, constraints, and surprising behavior.
- Keep runtime dependencies empty; development tools belong in the `dev` extra.

## Architecture and design

- Favor small, cohesive functions and classes. Prefer simplicity and readability.
- Use modules and functions when they provide sufficient structure. Introduce
  classes when they help manage state or model behavior.
- Keep SQL in repositories and database setup/migration modules, business rules
  in services, and terminal/filesystem presentation in CLI and rendering modules.
  Introduce abstractions when they clarify responsibilities or reduce duplication.
- Services own transactions; repositories must not commit independently. The
  caller owns the connection lifetime. Prepare report data before closing it.
- Group related functionality into cohesive modules. Introduce subpackages when
  several related modules justify the additional structure.

## Private data

- Keep question banks, raw assessment exports, databases, personal attempt
  history, and generated reports out of Git. Preserve the existing ignore rules;
  `private/README.md` is the documented exception within `private/`.
- Run `scripts/check_repo_safety.py` before preparing a commit for publication.
  Do not force-add private study data to bypass ignore rules.

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

## Development commands

Run commands from the repository root using the project virtual environment.
Install development tools with `uv sync --extra dev` or
`python -m pip install -e '.[dev]'` in the project virtual environment.

On Linux/macOS:

```bash
# Inspect CLI commands and run a quiz against an imported bank.
.venv/bin/python aws-study.py --help
.venv/bin/python aws-study.py quiz --cert CLF-C02 -n 10

# Build wheel and source distributions into dist/.
.venv/bin/python -m build
```

On Windows, replace `.venv/bin/python` with
`.\.venv\Scripts\python.exe`.

## Testing and verification

- Use `unittest`; name modules `test_<subject>.py` and methods `test_<behavior>`.
- Tests must use synthetic study data rather than private question banks or
  personal attempt history.
- Add or update tests for behavior changes that need regression coverage. Test
  observable behavior, failure rollback, and preservation of saved history.
- Run the relevant checks before completing a change and report any checks that
  could not be run. The coverage gate is 90%, with branch measurement enabled.
- The source-tree test runner needs `src/` on the Python import path. Tests also
  run against the installed package in CI on Python 3.12, 3.13, and 3.14 across
  Linux, Windows, and macOS.

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

## Commits and pull requests

- History uses plain-language summaries rather than mandatory prefixes. Prefer
  concise, action-oriented messages, such as "Preserve complete session context".
- Keep commits and PRs focused. Describe the concrete problem, resulting
  behavior, validation, and material compatibility risks in the PR description.
- Reference related issues when applicable; include terminal output when it
  helps demonstrate a CLI behavior change.
- Update relevant documentation, including `private/README.md` when local data
  or report workflows change. Report checks that could not run; do not merge
  while required CI checks are failing.
