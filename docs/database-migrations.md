# Database migrations

`migrate-db` accepts supported study databases v1–v7 and creates a validated copy
using the current SQLite schema, v7. Legacy v1 content/history is converted;
v2–v6 copies receive versioned upgrades in order. A v7 source produces a validated
copy, preserving drafts, flags, and archives. The source stays intact and the
destination must not exist. Preservation counts include active and archived
attempt history; drafts are counted separately when copying v6 or v7. Question-bank
JSON remains independently versioned at schema v2.

For a legacy v1 database, create a separate destination (it must not already exist):

```bash
python aws-study.py migrate-db \
  --source private/aws-study.db --dest private/aws-study-migrated.db
```

Import the curated sources into that destination:

```bash
python aws-study.py --db private/aws-study-migrated.db import-json \
  private/question-banks/aws/clf-c02/2026/aws-clf-c02-official-pretest-2026.json
python aws-study.py --db private/aws-study-migrated.db import-json \
  private/question-banks/aws/clf-c02/2026/aws-clf-c02-official-practice-question-set-2026.json
python aws-study.py --db private/aws-study-migrated.db import-json \
  private/question-banks/aws/clf-c02/2026/aws-clf-c02-official-practice-exam-2026.json
python aws-study.py --db private/aws-study-migrated.db stats --cert CLF-C02
python aws-study.py --db private/aws-study-migrated.db quiz --cert CLF-C02 -n 20 --year 2026
```

Use `--db private/aws-study-migrated.db` before the command while validating the
migration. After validation, the migrated database can be promoted to the normal
`private/aws-study.db` path so commands work without `--db`. Migration itself
never replaces the source. It preserves history and checks grading, counts, and foreign keys before publishing
the destination. Conflicts leave no destination file.

V1 did not distinguish imported verification from manual updates. Migration
preserves its effective verification as an explicit override. Use `source-verify`
to change that state later. Old generated taxonomy is discarded; importing the
curated files fills `area/topic` without altering attempts.

## Upgrade ordering and transactions

Each schema version is an ordered group of SQL steps. Schema v6 creation and its
draft conversion execute together before a future version begins. Canonical
upgrades run in one transaction; repeated initialization is harmless. Released
migration scripts are unchanged.

Legacy v1 needs a separate content/history mapping into a canonical destination.
Its unfinished-exam conversion runs after copying history, inside the existing
transaction. SQLite determines statement completeness so quoted semicolons and
trigger bodies cannot split a step incorrectly. Conversion never commits the
caller's transaction.

Older unfinished interactive exams become editable drafts. Their original
attempts and selected-answer records are archived with IDs, timestamps, notes,
confidence, correctness, and timing preserved. Those unfinished attempts stop
contributing to adaptive history. Completed exams, study history, and imported
baselines retain their existing attempts. Unknown historical durations remain
null until new active-question time is measured. Incompatible records cause the
upgrade to roll back. Legacy v1 databases require migration into a separate file.

## Session order and history

Older sessions keep their prior displayed order during database upgrades. Legacy
v1 migration maps each original question's answer order into the saved session,
including source variants with different orders. Canonical bank content and
source-specific answer order remain separate from session presentation.

Attempt validation checks active and archived history explicitly, including
foreign keys, membership, correctness, counts, and identity collisions. Only
active attempts affect adaptive selection. Services acquire a SQLite write lock
before checking session state; new attempt IDs are allocated inside the INSERT
above the maximum of both active and archived IDs. Writers therefore cannot
reuse an archived identity or concurrently allocate the same ID.

## Source provenance in v7

The additive v7 migration adds question-level explanations, exact upstream
verification dates, source classification, and JSON metadata to provenance.
Existing source classification is backfilled from canonical area/topic; no
verification dates are invented. Sources gain immutable snapshot fingerprints,
family identity, metadata, and a supersession link. Questions, answer IDs,
sessions, drafts, archives, rationales, and selected answers are preserved.
Older databases run v6 draft conversion before these additions.

## SQLite data model

Major tables:

- `certifications` — exam family/code/version
- `sources` — provenance and source freshness
- `questions` / `answers` — canonical content and curated area/topic
- `question_sources` / `question_source_answers` — source occurrences and explanations
- `sessions` / `session_questions` / `session_answers` — each quiz and its saved question/answer positions
- `session_responses` / `session_response_answers` — editable exam selections, confidence, timing, and flags; retained after submission
- `attempts` / `attempt_options` — finalized learning history (immediate in study mode)
- `archived_attempts` / `archived_attempt_options` — preserved pre-upgrade unfinished exam attempts, excluded from learning statistics
- `review_notes` — future human/AI annotations without mutating canonical question text

A question does **not** have a mutable `weak=true` flag. Weakness/mastery is derived from raw attempts, so the weighting algorithm can change later without losing history.

[Back to README](../README.md)
