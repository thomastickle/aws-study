# Session context format

New exports use context schema 3 independently of SQLite schema 6 and question-bank
schema 2. Every saved session question is present, including perfect sessions
and unanswered historical/study questions. Unsubmitted interactive exam drafts
cannot be exported.

## Envelope and question fields

The envelope contains `schema_version`, `purpose`, `session`, `continuation_prompt`,
and `questions`. Session metadata includes certification, mode/strategy, study
year, timestamps, source kind, requested count, label, and notes.

Questions retain `id`, `question_text`, `question_type`, `select_count`, `area`,
`topic`, `selection_group`, `position`, `flagged`, `answers`, `selected_answer_ids`,
`correct_answer_ids`, `sources`, and `result`. Question order follows the saved
session positions. Displayed answers retain `id`, `display_order`, `label`,
`answer_text`, boolean `is_correct`, and `rationale`. Labels use the saved session
order, independently of canonical/source order.

`result` contains boolean `is_correct`, nullable `confidence`, and nullable
`elapsed_ms`. Unanswered questions have `result: null` and no selected IDs.
Unknown imported durations remain null rather than being invented.

## Answer references and source provenance

Full answer details appear once in each question's `answers` array. Selected and
correct ID lists resolve against that array, in displayed order. Source answer
references contain `answer_id` and `source_order`, preserving source ordering
independently of displayed labels. A source reference inherits the displayed
answer's rationale unless it contains a `rationale` override; explicit `null`
or empty-string overrides preserve the source's absence of explanation.

For example, if `answers` contains an answer with `id: 52`, then
`"selected_answer_ids": [52]` identifies that choice, and a source reference
`{"answer_id": 52, "source_order": 1}` identifies its original source position.
No database lookup is needed. Source provenance retains verification origin and
validity years alongside source identity, reference, type, and verification
status/year. Redundant database linkage fields, fingerprints, and creation
timestamps are omitted from exported question/source records. Question/answer
IDs remain identifiers within the context pack; they need no external database.

Context schema 3 replaces the previous full selected/correct objects with ID
references. Existing version-2 exports remain untouched; new exports use only
version 3. SQLite schema 6 and question-bank schema 2 are unchanged. Both the
attached session context and local bank supply exact wording and answers; the
compact prompt supplies learning-state metadata and keeps its instruction to
avoid revealing prior answers before you respond.

## Report and tutoring behavior

Markdown reports distinguish misses from correct low/medium-confidence
reinforcement candidates. The compact prompt prioritizes misses, then low- and
medium-confidence correct answers. Routine correct/high-confidence answers stay
in context without cluttering the summary. Summary entries contain learning-state
metadata, not selected/correct answers or rationales. The attached context or local
bank supplies exact content, with reasoning requested before revealing answers.

Prepared context, session, and attempt records use standard-library `TypedDict`
types. `SessionData` owns each collection once; rendering needs no database access.
Existing exports are never rewritten. Regenerating a report creates another
exam-code/timestamp directory.

[Back to README](../README.md)
