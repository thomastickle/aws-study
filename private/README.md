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
Canonical schema-2 through schema-5 databases upgrade transactionally to schema 6 on opening; the
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

Each new context JSON retains every saved session question in quiz order, plus
its saved answer letters, complete answers/rationales, source provenance,
selected/correct answers, and result (correctness, confidence, elapsed time).
Context questions include boolean `flagged` state. Unanswered historical/study
questions remain present with `result: null`. Context schema version
2 is retained with these additive fields. Existing reports are not rewritten.

The compact prompt prioritizes misses, then correct low- and medium-confidence
answers, without including selected or correct answer text. The Markdown report
keeps detailed missed-answer review and adds reinforcement candidates only when
needed. Incomplete study/baseline reports show answered versus saved counts and score only
answered questions. No older-history analysis is added.

Schema 6 persists exam drafts, selections, confidence, flags, and active-question
time. Unfinished interactive exam attempts from older databases move into an
archive with their original metadata intact, and their selections become
editable drafts. Completed sessions, study attempts, and imported baseline
history remain unchanged. Active and archived rows both count toward migration
preservation checks; only active finalized attempts drive adaptive learning.

Starting an exam offers saved drafts for the same certification. Use
`python aws-study.py quiz --resume <id>` to resume directly. A valid selection
saves before confidence entry; Ctrl+C, EOF, and `:q` keep saved drafts. Final
review allows editing and flagging and requires complete valid answers plus
explicit confirmation before creating attempts. Its compact index marks flagged
or incomplete questions with `*`. Choose a number to see the full question;
selected options are bold and marked `>`. Use `:n` to skip, `:p` for the previous
question, and `:r` to return to the index. Finishing an edit returns to the index.
Drafts and flags remain after
submission. No new runtime packages are needed.

Unfinished interactive exams cannot generate learning reports or prompts;
complete them through resume first. Quitting produces no report bundle and does
not change adaptive history. Existing export files remain untouched.
