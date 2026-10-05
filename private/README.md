# Private study data

Everything in this directory except this README is ignored by Git.

Recommended layout:

- `question-banks/` — imported/exported question banks and source-derived JSON
- `aws-study.db` — SQLite database containing questions and attempt history
- `reports/` — per-session Markdown reports, compact ChatGPT prompts, and context packs

Do not force-add these files to a public repository. The code is designed so the repository can be public while the question text, answer rationales, and personal attempt history stay local.
