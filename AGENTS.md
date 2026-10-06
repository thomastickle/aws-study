# Project guidance

This project is an application for studying and practicing for AWS certification
exams. The current question bank focuses on AWS Certified Cloud Practitioner.

## Requirements

- Use the Python version declared in `pyproject.toml`, currently Python 3.14+.
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

## Verification

Run commands from the repository root using the project virtual environment.
The test runner needs `src/` on the Python import path.

On Linux/macOS:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_repo_safety.py
```

On Windows PowerShell:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts/check_repo_safety.py
```

Run the repository safety check before preparing a commit for publication.
