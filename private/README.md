# Private study data

Everything in this directory except this README is ignored by Git.

Recommended layout:

- `question-banks/` — imported/exported question banks and source-derived JSON
- `aws-study-v2.db` — separately migrated database for testing; keep the original until satisfied
- `question-banks/aws/clf-c02/2026/` — the three curated schema-v2 official banks
- `aws-study.db` — SQLite database containing questions and attempt history
- `reports/<exam-code>/YYYYMMDD-HHMMSS/` — one folder per export (24-hour clock), containing `session-<id>-report.md`, `session-<id>-prompt.txt`, and `session-<id>-context.json`

Do not force-add these files to a public repository. The code is designed so the repository can be public while the question text, answer rationales, and personal attempt history stay local.
