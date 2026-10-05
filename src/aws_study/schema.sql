PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS certifications (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'AWS',
    code TEXT NOT NULL,
    name TEXT,
    version TEXT,
    active_from_year INTEGER,
    active_to_year INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, code)
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    certification_id INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    source_key TEXT NOT NULL,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'unknown',
    source_file TEXT,
    published_year INTEGER,
    observed_year INTEGER,
    retrieved_at TEXT,
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    verified_at TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(certification_id, source_key)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY,
    certification_id INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_question_number INTEGER,
    external_key TEXT NOT NULL,
    question_text TEXT NOT NULL,
    question_type TEXT NOT NULL CHECK(question_type IN ('single_select','multi_select')),
    domain TEXT,
    topic TEXT,
    concept TEXT,
    valid_from_year INTEGER,
    valid_to_year INTEGER,
    verification_status TEXT,
    verified_year INTEGER,
    dedup_group TEXT,
    dedup_role TEXT NOT NULL DEFAULT 'canonical',
    variant_group TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0,1)),
    content_hash TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, external_key)
);

CREATE INDEX IF NOT EXISTS idx_questions_cert ON questions(certification_id);
CREATE INDEX IF NOT EXISTS idx_questions_year ON questions(valid_from_year, valid_to_year, verified_year);
CREATE INDEX IF NOT EXISTS idx_questions_topic ON questions(topic);
CREATE INDEX IF NOT EXISTS idx_questions_hash ON questions(content_hash);

CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    option_order INTEGER NOT NULL,
    option_label TEXT,
    option_text TEXT NOT NULL,
    is_correct INTEGER NOT NULL CHECK(is_correct IN (0,1)),
    rationale TEXT,
    UNIQUE(question_id, option_order)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS question_tags (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY(question_id, tag_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    certification_id INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    started_at TEXT,
    completed_at TEXT,
    target_year INTEGER,
    mode TEXT NOT NULL DEFAULT 'exam',
    strategy TEXT NOT NULL DEFAULT 'adaptive',
    requested_count INTEGER,
    session_label TEXT,
    source_kind TEXT NOT NULL DEFAULT 'interactive',
    notes TEXT
);

CREATE TABLE IF NOT EXISTS session_questions (
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    selection_weight REAL,
    PRIMARY KEY(session_id, position),
    UNIQUE(session_id, question_id)
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    attempted_at TEXT,
    is_correct INTEGER NOT NULL CHECK(is_correct IN (0,1)),
    confidence TEXT CHECK(confidence IN ('low','medium','high') OR confidence IS NULL),
    elapsed_ms INTEGER,
    source_kind TEXT NOT NULL DEFAULT 'interactive',
    note TEXT
);

CREATE TABLE IF NOT EXISTS attempt_options (
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    option_id INTEGER NOT NULL REFERENCES options(id) ON DELETE CASCADE,
    selected INTEGER NOT NULL DEFAULT 1 CHECK(selected IN (0,1)),
    PRIMARY KEY(attempt_id, option_id)
);

CREATE TABLE IF NOT EXISTS review_notes (
    id INTEGER PRIMARY KEY,
    question_id INTEGER REFERENCES questions(id) ON DELETE CASCADE,
    certification_id INTEGER NOT NULL REFERENCES certifications(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    note_type TEXT NOT NULL DEFAULT 'concept',
    note_text TEXT NOT NULL
);
