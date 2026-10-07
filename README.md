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

The pre-commit hook rejects obvious private database/question-source files if
they somehow become tracked. The check also rejects generated reports and
assessment exports, and fails if Git cannot be inspected. It checks tracked
paths rather than scanning file contents for secrets.

## Tests and builds

GitHub Actions runs on pushes, pull requests, and manual dispatch. It installs
the package and runs the test suite and CLI checks on Python 3.12, 3.13, and 3.14
across Linux, Windows, and macOS. Pip downloads and dependency wheels are cached
between runs, using `pyproject.toml` for pip dependency cache inputs. Pip does
not install from `uv.lock`; that lock remains available for local uv development.
A separate Linux/Python 3.12 quality job runs Ruff lint and format
checks, mypy, branch coverage, optimized-Python tests, and the repository safety
check. After tests and quality checks pass, it builds a wheel and source archive on
Python 3.12, checks package metadata, and attaches both files to the workflow run
for 14 days. The test and build jobs use separate cache keys so the build tools
can be saved independently.

Python 3.12 is the development default in `.python-version`. New language or
standard-library features must work on 3.12 unless the minimum is raised.

Install development-only tools with `uv sync --extra dev`, or with
`.venv/bin/python -m pip install -e '.[dev]'` in a pip-enabled virtual environment.
The existing `uv.lock` records the development dependencies. Runtime dependencies
remain empty.

Run local checks from the repository root:

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

The branch-coverage gate is 90%. `jsonschema` validates the public example/schema in development;
the application continues to validate imports using standard-library code.

Packaging uses the same development extra:

```bash
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*
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

Local study data lives under `private/` and is ignored by Git. New databases
use database schema 7 (with schema-v2 question-bank inputs). Existing canonical
schema-2 through schema-6 databases upgrade transactionally on opening. Schema 5
introduced unique session/question attempts and nonnegative elapsed time; schema
6 adds persisted exam drafts and archived legacy attempts. Schema 7 adds source
classification, exact verification dates, explanations, and immutable snapshots.

### Migrating databases

`migrate-db` creates a validated copy and never replaces its source. The destination
must not exist. Legacy v1 databases require this command; canonical v2–v6 databases
also upgrade automatically when opened.

```bash
python aws-study.py migrate-db \
  --source private/aws-study.db --dest private/aws-study-migrated.db
```

See [database migrations](docs/database-migrations.md) for preservation rules,
legacy verification, validation, and the data model.

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

`exam` mode saves editable drafts and shows no correctness feedback until final
submission. `study` mode reveals correctness and stored rationales immediately,
recording each answer as an attempt. Both modes require exactly the question's
stored selection count; duplicate input tokens count once.

### Exam navigation and final review

At a question, enter choices normally, or use these case-insensitive commands:

| Command | Action |
| --- | --- |
| `:f` / `:flag` | Toggle the review flag without answering |
| `:n` / `:next` | Move forward; unanswered questions may be skipped |
| `:p` / `:previous` | Move backward |
| `:r` / `:review` | Open the review list |
| `:q` / `:quit` | Save drafts and quit |

Plain `F` remains answer choice F. Valid selections save before the confidence
prompt, so Ctrl+C or EOF there keeps the answer. C/E/U and their full labels set
confidence; Enter retains the current value, and `none` clears it. Revisiting
preserves existing confidence unless explicitly changed. Active-question time
accumulates across visits, including confidence entry; persistence, review, and
offline time are excluded. Answer changes and their elapsed intervals save in
one transaction.

The compact review index lists selections in original question order. A `*`
marks flagged, unanswered, or incomplete questions, with a status label explaining
why. Enter a question number (or `R <n>`) to view its full question and options;
saved selections are bold in an interactive terminal and marked `>` in all output.
Use `:n` to skip to the next question, `:p` for the previous question, or `:r`
to return to the index. Finishing an edit also returns to the index. From the
index, `F <n>` toggles a flag, `S` submits, and `Q` quits. Submission requires
valid answers for every question and an explicit yes at `Submit exam? [y/N]`.
It atomically creates final attempts and marks completion; drafts and flags
remain available as historical state. Repeated submission never duplicates
attempts. Flags express a desire to review and do not automatically imply weakness.

### Saved exams and resume

Starting an exam offers unfinished drafts for the requested certification, with
session IDs, answered/total counts, and flag counts. Choose a session ID to
resume, `N` to start another exam while retaining drafts, or `Q` to quit.

Resume directly without selecting or reshuffling questions:

```bash
python aws-study.py quiz --resume 12
```

Selection options such as `--cert`, `--count`, `--mode`, and `--seed` cannot be
combined with `--resume`; display width and report destination may be changed.
Resume begins at the first unanswered or invalid question, or opens review if
all answers are valid. Quitting, Ctrl+C, and EOF preserve saved drafts without
scoring or exporting reports. Unsaved input and time since the last checkpoint
cannot survive an abrupt process kill.

Only submitted exam attempts affect adaptive history. `report` and `prompt`
refuse unfinished interactive exams to avoid exposing correct answers; use
`quiz --resume <id>` first. Completed and imported baseline reports remain
available.

Answer choices are shuffled when each session is created, in both modes. Letters
A/B/C/etc. reflect that session's presentation. The saved order stays stable when
questions are loaded again and when reports/context packs are regenerated;
grading uses answer IDs. `--seed` reproduces both question selection and answer
order when the bank/history are unchanged. An ordinary random shuffle may
occasionally produce the original order.

Older sessions preserve their displayed order during upgrades; see
[database migrations](docs/database-migrations.md#session-order-and-history).

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

Learning reports are generated after submission.

1. `session-<id>-report.md` — score plus the **curated area/topic for each missed question**, selected answer, correct answer, confidence, an optional section for correct low- or medium-confidence answers, and a compact continuation prompt.
2. `session-<id>-prompt.txt` — only the compact ChatGPT continuation prompt: misses first, then correct low- and medium-confidence answers. Its summaries contain topics, sources, and confidence, without selected or correct answers.
3. `session-<id>-context.json` — a complete schema-v3 session record containing every saved question in quiz order, with the saved answer order/letters, full wording, answers, rationales, and all source occurrences. Each question includes `position`, `result` (correctness, confidence, elapsed milliseconds), `selected_answer_ids`, and `correct_answer_ids`. Questions include a boolean `flagged` field. Unanswered questions in historical/study reports have `result: null` and an empty selected-ID list.

See [context format](docs/context-format.md) for the complete schema-3 fields,
answer references, source rationale inheritance, and compatibility rules.

Regenerating creates another export folder. If names collide within the same
second, a numeric suffix is added to the timestamp folder. `--reports PATH`
changes the parent directory; the exam-code/timestamp structure still applies.
The target study year remains in the report and context file.

The JSON preserves the full session while the prompt stays focused on learning
priorities. Correct high-confidence answers and correct answers with unrecorded
confidence remain in the JSON but are omitted from the prompt. Reinforcement
summaries contain no answer text; detailed answers remain in the report's missed
areas and the JSON. The tutoring instructions still require reasoning before
revealing answers, even when the full context is attached.

Incomplete study/baseline reports show answered versus saved question counts. Scores use only
answered questions; unanswered questions are not counted as incorrect. Reports
summarize the selected session, without calculating older weaknesses or changing
adaptive selection. Regenerated exports use the bank content and provenance
available at export time. Existing export files remain untouched.

Regenerate the latest report bundle:

```bash
python aws-study.py report latest
```

For `report latest` and `prompt latest`, latest means the newest reportable
interactive session. Unsubmitted exams and imported baselines are skipped;
partial or empty study sessions remain eligible. Explicit draft IDs still return
the not-submitted error. A missing reportable session produces a useful error.

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

A quiz selects at most one question with the same normalized stem, even when
answer choices differ. All variants stay in the bank; the sampler randomly picks
one using the chosen strategy's weights. Stored sessions and history are unchanged.
If greedy weighted picks block a full quiz, a matching fallback replaces those
picks with a valid combination. The quiz fills the requested count whenever
possible, otherwise it uses the largest set satisfying both constraints.

For confirmed equivalents with slightly different wording, v2 bank records can
include an optional curated `selection_group` key. Such variants also cannot
appear together. Grouping does not change content identity, grading, taxonomy,
verification, or history. Re-importing a record without the field preserves an
existing group; contradictory imported group keys are errors.

To explicitly assign or edit a group in an existing bank:

```bash
python aws-study.py question-group \
  --cert CLF-C02 --key confirmed-variants 12 48
```

Use the actual question IDs to group. Keys are scoped to certification. The
application does not infer equivalent wording through fuzzy matching.

`random` ignores mastery weighting. `weak` limits the pool to previously missed questions. `new` limits it to questions with no attempt history.

Legacy imported-baseline attempts survive database migration and still inform
adaptive selection. V2 source banks contain question content, not attempt history.

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

That means a question verified in 2026 will *not* silently be considered verified-current for a 2027 target exam. Reverify it for the new year, or import updated verification for a source that
has no explicit override. `--include-unverified` exists as an explicit escape hatch, not the default.

This is deliberate because AWS service behavior, terminology, exam blueprints, and canonical answers can change.

## Multiple certifications

The database is not tied to Cloud Practitioner. Add/import separate banks under distinct exam codes:

```bash
python aws-study.py cert-add --code EXAM-CODE --name "Another AWS certification"
python aws-study.py import-json private/question-banks/another-bank.json
```

Questions, sources, attempts, sessions, reports, and adaptive weights stay separated by certification.

## Importing question banks

The importer accepts **question-bank JSON schema v2 only**. Certification, source and curated
classification come from the file. A synthetic example is in
`examples/question-bank.sample.json`.
`examples/question-bank.schema.json` describes the v2 structure. The optional provenance fields described in the
[conversion guide](docs/question-bank-conversion.md) are validated and retained;
other additional fields remain accepted and ignored. Runtime/history fields are
rejected, and Python validation also enforces normalized answer uniqueness,
unique source references, and equality between `select_count` and correct answers.

The offline `convert cloudcertprep` command produces standard v2 banks; see the
[conversion guide](docs/question-bank-conversion.md). There is no legacy v1
question-bank file migration command. The old combined
`aws-clf-c02-official-2026.json` is obsolete v1 input; the three curated source
files under `private/question-banks/aws/clf-c02/2026/` replace it. Its embedded
selected answers, confidence, and timing belong in database history, which
`migrate-db` preserves from a legacy database. Bank-format conversion also needs
curated classification metadata; importing v2 never recreates personal attempts.

```bash
python aws-study.py import-json private/question-banks/my-bank.json --dry-run
python aws-study.py import-json private/question-banks/my-bank.json
```

Dry-run validates and simulates against an in-memory copy of an existing database,
opened read-only. It does not create a database when the path is absent. The JSON
summary reports new canonical questions, existing matches, new provenance links,
conflicts, and invalid records. Invalid/conflicting previews exit with status 2.
No part of a conflicting bank is saved.

Identity uses normalized question text plus the complete unordered answer set,
scoped to certification. Normalization applies Unicode NFKC, whitespace folding,
and case folding; punctuation remains significant. Original displayed text is
preserved. Answer order and correctness are excluded from identity; conflicting
correct-answer sets, types, or selection counts are errors. Source taxonomy
differences are retained in provenance; existing non-null canonical classification
is never overwritten by later imports. Changed content under an existing source reference is also an error.

One canonical question can have multiple source occurrences, including different
references in the same source. Each retains answer order and rationales. Quiz
selection sees each canonical question once. Re-imports are idempotent. Items
without a source reference match within their source by content rather than array
position. Imports are additive: omission from a later file does not delete
questions, provenance, or history.

The first source establishes canonical answer order and rationales. Subsequent
imports retain that presentation and update the source-specific explanations.
Immutable source snapshots support explicit replacement through
`--supersede-source`; ordinary imports never silently rewrite changed content.

An explicit `source-verify` update survives later bank imports, including older
verification metadata. It applies to all occurrences from that source. A question
is eligible when any one occurrence meets the requested year/freshness rules.

Verification states supported today:

- `official_current`
- `verified_current`
- `official_older`
- `unverified`
- `stale`
- `superseded`

No inferred classification or semantic duplicate/alternate metadata is imported.

## Code organization

`src/aws_study/` separates CLI and terminal presentation from shared services and
SQLite repositories. `quiz.py` handles study mode, `exam_ui.py` handles resumable
exams, and `quiz_rendering.py` shares choice parsing and rendering. `ReportService`
prepares typed `SessionData`; the renderer needs no live database connection.
See [Repository Guidelines](AGENTS.md) for contributor rules and architecture
constraints, and [database migrations](docs/database-migrations.md) for persistence
details. Tests use synthetic data and preserve history, rollback, and selection
invariants.

## Topic labels

Classification comes from each source file's curated `classification.area` and
`classification.topic`. Import does not infer labels from question or distractor
keywords. Migration leaves classification empty until curated data is imported.
Reports and stats use broad area plus specific topic. Reports can also show an
answer-boundary comparison as separate coaching context.

## Current private corpus included in this package

The private bank bundled for the user contains the three already-extracted official Skill Builder sets:

- Official Pretest — 65
- Official Practice Question Set — 20
- Official Practice Exam — 65

That is 150 source records and 150 canonical questions under deterministic
matching. Same-stem variants with different complete answer sets remain separate.

The question JSON and SQLite DB are intentionally under `private/` and ignored by Git.

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
  --source-key official-practice-exam-2026 \
  --year 2027 \
  --status verified_current
```

This is intentionally manual. The program should not infer that an old question remains current merely because it still sounds plausible.
