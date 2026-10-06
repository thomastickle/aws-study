# Private study data

Everything in this directory except this README is ignored by Git.

Recommended layout:

- `question-banks/` — imported/exported question banks and source-derived JSON
- `question-banks/aws/clf-c02/2026/` — the three curated schema-v2 official banks
- `aws-study.db` — the validated working database, including migrated history and subsequent quiz runs; the CLI uses this path by default
- `reports/<exam-code>/YYYYMMDD-HHMMSS/` — one folder per export (24-hour clock), containing `session-<id>-report.md`, `session-<id>-prompt.txt`, and `session-<id>-context.json`

Do not force-add these files to a public repository. The code is designed so the repository can be public while the question text, answer rationales, and personal attempt history stay local.

The bank retains question variants and their separate answers/history. Quiz
selection allows one per normalized stem or curated `selection_group` in each
run, choosing among eligible variants randomly according to the quiz strategy.
Source files may carry `selection_group` for confirmed wording variants.
Schema-2, schema-3, and schema-4 databases upgrade transactionally to schema 5 on opening; the
original legacy database still requires explicit migration into a separate file.
New sessions shuffle answers and save their order, so answer letters agree with
later reports. Existing sessions retain their displayed order and history.
Schema 5 enforces one attempt per session/question and nonnegative elapsed time.
Upgrades reject incompatible history without altering or discarding records.

The validated migration has been promoted to `aws-study.db`. The obsolete legacy
and test database copies, experimental TUI database, and verification reports
have been removed. The latest report bundle is retained; earlier reports can be
regenerated from the session history using `python aws-study.py report <id>`.

`question-banks/aws-clf-c02-official-2026.json` is an obsolete combined v1 bank.
Use the three curated v2 files in `question-banks/aws/clf-c02/2026/` for imports.
The old file also embeds personal attempt data; there is no bank-file migration
command. Database history is preserved by `migrate-db`, separately from the bank
format. The obsolete file is retained as a legacy archive.
