# aws-study

A local, SQLite-backed question-bank and mini-exam tool designed for certification study without turning ChatGPT conversation history into the database.

The first bundled private bank is the AWS Certified Cloud Practitioner material already extracted from the user's official Skill Builder assessment exports. The application itself is certification-agnostic: separate exam codes, versions, sources, verification years, topics, and attempt histories can coexist in one database.

## Why Python for v0.1?

This version uses Python 3.12+ and only the standard library at runtime. SQLite
is built into Python. If a browser UI is added later, the SQLite schema and
quiz/report model can remain the backend contract for a TypeScript/React front end.

## Safety / publishing model

The **code can be public** while question banks and study history remain local.

`.gitignore` excludes:

- `private/*` except `private/README.md`
- SQLite databases and WAL files
- raw HTML/MHTML assessment exports
- common import/question-bank directories
- generated reports

There is also a repository safety check:

```bash
python scripts/check_repo_safety.py
```

For an extra guard, after initializing Git you can enable the included hook:

```bash
git config core.hooksPath .githooks
```

The pre-commit hook rejects obvious private database/question-source files if they somehow become tracked.

## Tests and builds

GitHub Actions runs on pushes, pull requests, and manual dispatch. It installs
the package and runs the test suite and CLI checks on Python 3.12, 3.13, and 3.14
across Linux, Windows, and macOS. Pip downloads and dependency wheels are cached
between runs. After the tests pass, it builds a wheel and source archive on
Python 3.12, checks package metadata, and attaches both files to the workflow run
for 14 days. The test and build jobs use separate cache keys so the build tools
can be saved independently.

Python 3.12 is the development default in `.python-version`. New language or
standard-library features must work on 3.12 unless the minimum is raised.

To test locally from the repository root:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Packaging uses development tools downloaded separately from runtime dependencies:

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

## License

The code is licensed under the [MIT License](LICENSE).

## Quick start

From the project directory:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe .\aws-study.py stats --cert CLF-C02
```

Linux/macOS:

```bash
./setup.sh
.venv/bin/python aws-study.py stats --cert CLF-C02
```

There are **no runtime package downloads**. The top-level `aws-study.py` launcher loads the code directly from `src/`. The `pyproject.toml` remains available if you later want to package/install it conventionally.

A prebuilt local database is included in this package at `private/aws-study.db`. Because it lives under `private/`, Git ignores it.

Check it:

```bash
python aws-study.py stats --cert CLF-C02
```

Run a 20-question adaptive mini exam:

```bash
python aws-study.py quiz --cert CLF-C02 -n 20 --year 2026
```

Run a pure random exam instead:

```bash
python aws-study.py quiz --cert CLF-C02 -n 20 --year 2026 --strategy random
```

Other useful strategies:

```bash
python aws-study.py quiz --cert CLF-C02 -n 15 --year 2026 --strategy weak
python aws-study.py quiz --cert CLF-C02 -n 15 --year 2026 --strategy new
python aws-study.py quiz --cert CLF-C02 -n 20 --year 2026 --mode study
```

`exam` mode waits until the end to show misses. `study` mode reveals the correct answer and stored rationale after each response.

Quiz questions, choices, and feedback wrap at word boundaries within 80 columns
by default. Narrower terminals reduce the width automatically, leaving two
columns at the right edge. Continuation lines align with their question or
answer text, and existing paragraph breaks are preserved. The width is checked
again as output is printed, so resizing the terminal affects subsequent text.

Use `--width` to choose another maximum, for example:

```bash
python aws-study.py quiz --cert CLF-C02 --width 100
```

Hyphenated names and long unbroken tokens stay intact; a token longer than the
available line may exceed the wrapping width.

## What happens after each quiz?

Exports are grouped by exam code, then by local export time (24-hour clock):

```text
private/reports/CLF-C02/20261006-143012/
  session-5-report.md
  session-5-prompt.txt
  session-5-context.json
```

1. `session-<id>-report.md` — score plus the **area/concept for each missed question**, selected answer, correct answer, confidence, and a compact continuation prompt.
2. `session-<id>-prompt.txt` — only the compact ChatGPT continuation prompt.
3. `session-<id>-context.json` — a deliberately small attachment containing only missed and low-confidence questions, with their exact local wording/options/rationales.

Regenerating creates another export folder. If names collide within the same
second, a numeric suffix is added to the timestamp folder. `--reports PATH`
changes the parent directory; the exam-code/timestamp structure still applies.
The target study year remains in the report and context file.

This keeps the long-lived corpus and attempt history in SQLite while allowing a fresh ChatGPT session to receive only the state it needs.

Regenerate the latest report bundle:

```bash
python aws-study.py report latest
```

Print only the continuation prompt:

```bash
python aws-study.py prompt latest
```

## Adaptive selection

The default `adaptive` selector increases probability for:

- new questions
- questions with a high historical miss rate
- the last attempt being wrong
- low-confidence answers
- items not seen recently

It decreases probability after a correct streak, but never permanently removes mastered questions.

`random` ignores mastery weighting. `weak` limits the pool to previously missed questions. `new` limits it to questions with no attempt history.

Imported assessment results can be stored as historical baseline attempts, so the selector can immediately use the mistakes from the original assessment.

## Year and exam-version protection

Freshness is intentionally strict.

Each question/source can record:

- certification/exam code, e.g. `CLF-C02`
- source type and provenance
- published/observed year
- verification status
- verification year
- valid-from / valid-to year
- stale/superseded state

By default, a 2026 mini exam only selects questions marked `official_current` or `verified_current` **and verified in 2026 or later**.

That means a question verified in 2026 will *not* silently be considered verified-current for a 2027 target exam. Reverify/reimport it for the new year. `--include-unverified` exists as an explicit escape hatch, not the default.

This is deliberate because AWS service behavior, terminology, exam blueprints, and canonical answers can change.

## Multiple certifications

The database is not tied to Cloud Practitioner. Add/import separate banks under distinct exam codes:

```bash
python aws-study.py cert-add --code EXAM-CODE --name "Another AWS certification"
python aws-study.py import-json private/question-banks/another-bank.json \
  --cert EXAM-CODE \
  --observed-year 2026 \
  --verified-year 2026 \
  --verification-status verified_current \
  --source-type reputable-third-party
```

Questions, sources, attempts, sessions, reports, and adaptive weights stay separated by certification.

## Importing question banks

The v0.1 importer accepts the normalized JSON shape used by the bundled bank. A minimal example is in `examples/question-bank.sample.json`.

Import a bank:

```bash
python aws-study.py import-json private/question-banks/my-bank.json \
  --cert CLF-C02 \
  --cert-name "AWS Certified Cloud Practitioner" \
  --observed-year 2026 \
  --verified-year 2026 \
  --source-type official \
  --verification-status official_current
```

Verification states supported today:

- `official_current`
- `verified_current`
- `official_older`
- `unverified`
- `stale`
- `superseded`

The importer preserves duplicate/variant metadata from the current normalized bank. Normal quizzes use canonical questions only by default.

## Code organization

Command parsing and terminal output live in `cli.py` and `quiz.py`.
Services coordinate quiz rules, source verification, and report assembly.
`importers.py` normalizes JSON, classifies content, and coordinates imports.
SQL belongs in the feature repositories; `db.py` only opens connections and
initializes the schema. All repository query parameters use named binds.

Repositories share a caller-owned connection and return models or documented
export records. Their write methods do not commit: the operation that combines
those writes owns the transaction. An import saves the entire bank atomically,
and source verification updates provenance and questions together. CLI commands
close their connections on success and failure.

`ReportService` prepares a `ReportBundle`; `reporting.py` renders its text and
writes the files without accessing SQLite. Statistics and source lists likewise
receive read models rather than formatting database rows in the CLI.

Regression tests use synthetic data and exercise rollback, re-imported history,
certification isolation, report exports, and the SQL boundary.

## SQLite data model

Major tables:

- `certifications` — exam family/code/version
- `sources` — provenance and source freshness
- `questions` / `options` — immutable-ish canonical question content
- `tags` / `question_tags` — lightweight taxonomy
- `sessions` / `session_questions` — each generated quiz
- `attempts` / `attempt_options` — actual learning history
- `review_notes` — future human/AI annotations without mutating canonical question text

A question does **not** have a mutable `weak=true` flag. Weakness/mastery is derived from raw attempts, so the weighting algorithm can change later without losing history.

## Topic labels

v0.1 contains a conservative keyword classifier used only to make reports more useful, for labels such as:

- Migration
- Hybrid networking
- EC2 purchasing & tenancy
- Security services
- S3 & object storage
- Governance & frameworks

These are study aids, **not claimed to be official AWS exam-domain mappings**. The schema has a separate `domain` field so official domains/objectives can be imported when a trustworthy source provides them.

## Current private corpus included in this package

The private bank bundled for the user contains the three already-extracted official Skill Builder sets:

- Official Pretest — 65
- Official Practice Question Set — 20
- Official Practice Exam — 65

That is 150 source records and 148 canonical questions after the two known duplicate pairs are marked as alternates. Same-stem variants with different answer sets remain separate.

The question JSON and SQLite DB are intentionally under `private/` and ignored by Git.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Likely next increments

The schema already leaves room for the useful next steps:

- importer for additional normalized third-party banks with explicit provenance/year
- exact Skill Builder HTML-to-JSON converter
- official exam domain/objective mapping when the source supplies it
- near-duplicate review queue
- spaced-repetition tuning based on days and confidence
- richer session analytics
- optional local web UI
- export/import for moving the private DB between machines

The core rule should remain: **maximize currently trustworthy questions, not merely total question count.**

## Inspecting and re-verifying source freshness

List source provenance/freshness:

```bash
python aws-study.py sources --cert CLF-C02
```

After actually checking an older source against the target year's current AWS material, mark it explicitly:

```bash
python aws-study.py source-verify \
  --cert CLF-C02 \
  --source-key practice_exam \
  --year 2027 \
  --status verified_current
```

This is intentionally manual. The program should not infer that an old question remains current merely because it still sounds plausible.
