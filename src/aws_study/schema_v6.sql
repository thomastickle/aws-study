-- Drafts are exam state; only submitted attempts drive learning history.
CREATE TABLE session_responses (
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    confidence TEXT CHECK(confidence IN ('low','medium','high') OR confidence IS NULL),
    elapsed_ms INTEGER DEFAULT 0 CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
    flagged INTEGER NOT NULL DEFAULT 0 CHECK(flagged IN (0,1)),
    first_answered_at TEXT,
    updated_at TEXT,
    PRIMARY KEY(session_id, question_id),
    FOREIGN KEY(session_id, question_id)
        REFERENCES session_questions(session_id, question_id) ON DELETE CASCADE
);
CREATE TABLE session_response_answers (
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    answer_id INTEGER NOT NULL,
    PRIMARY KEY(session_id, question_id, answer_id),
    FOREIGN KEY(session_id, question_id)
        REFERENCES session_responses(session_id, question_id) ON DELETE CASCADE,
    FOREIGN KEY(session_id, question_id, answer_id)
        REFERENCES session_answers(session_id, question_id, answer_id) ON DELETE CASCADE
);
-- Original unfinished exam attempts remain immutable audit records.
CREATE TABLE archived_attempts (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    attempted_at TEXT,
    is_correct INTEGER NOT NULL CHECK(is_correct IN (0,1)),
    confidence TEXT CHECK(confidence IN ('low','medium','high') OR confidence IS NULL),
    elapsed_ms INTEGER CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0),
    source_kind TEXT NOT NULL DEFAULT 'interactive',
    note TEXT
);
CREATE TABLE archived_attempt_options (
    attempt_id INTEGER NOT NULL REFERENCES archived_attempts(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
    selected INTEGER NOT NULL DEFAULT 1 CHECK(selected IN (0,1)),
    PRIMARY KEY(attempt_id, option_id)
);
PRAGMA user_version = 6;
