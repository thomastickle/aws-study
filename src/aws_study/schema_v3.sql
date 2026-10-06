-- A curated selection constraint does not change canonical question identity.
CREATE TABLE question_selection_groups (
    question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
    group_key TEXT NOT NULL CHECK(length(trim(group_key)) > 0)
);
CREATE INDEX idx_question_selection_group ON question_selection_groups(group_key);
PRAGMA user_version = 3;
