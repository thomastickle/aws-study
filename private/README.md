# Private study data

Everything in this directory except this README is ignored by Git.

Recommended layout:

- `question-banks/` — imported/exported question banks and source-derived JSON
- `aws-study-v2.db` — separately migrated database for testing; keep the original until satisfied
- `question-banks/aws/clf-c02/2026/` — the three curated schema-v2 official banks
- `aws-study.db` — SQLite database containing questions and attempt history
- `reports/<exam-code>/YYYYMMDD-HHMMSS/` — one folder per export (24-hour clock), containing `session-<id>-report.md`, `session-<id>-prompt.txt`, and `session-<id>-context.json`

Do not force-add these files to a public repository. The code is designed so the repository can be public while the question text, answer rationales, and personal attempt history stay local.

The bank retains question variants and their separate answers/history. Quiz
selection allows one per normalized stem or curated `selection_group` in each
run, choosing among eligible variants randomly according to the quiz strategy.
Source files may carry `selection_group` for confirmed wording variants.
Schema-2 and schema-3 databases upgrade additively to schema 4 on opening; the
original legacy database still requires explicit migration into a separate file.
New sessions shuffle answers and save their order, so answer letters agree with
later reports. Existing sessions retain their displayed order and history.
