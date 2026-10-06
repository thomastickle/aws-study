-- Validate existing history before adding invariants; never discard attempts.
CREATE TEMP TABLE schema_v5_attempt_check (
    elapsed_ms INTEGER CONSTRAINT elapsed_ms_must_be_nonnegative
        CHECK(elapsed_ms IS NULL OR elapsed_ms >= 0)
);
INSERT INTO schema_v5_attempt_check(elapsed_ms) SELECT elapsed_ms FROM attempts;
DROP TABLE schema_v5_attempt_check;

CREATE UNIQUE INDEX idx_attempt_session_question ON attempts(session_id, question_id);
CREATE INDEX idx_attempt_question_recent ON attempts(question_id, id DESC);

-- Triggers enforce the check without rebuilding a table referenced by history.
CREATE TRIGGER attempts_nonnegative_elapsed_insert
BEFORE INSERT ON attempts WHEN NEW.elapsed_ms < 0
BEGIN
    SELECT RAISE(ABORT, 'Elapsed time cannot be negative');
END;
CREATE TRIGGER attempts_nonnegative_elapsed_update
BEFORE UPDATE OF elapsed_ms ON attempts WHEN NEW.elapsed_ms < 0
BEGIN
    SELECT RAISE(ABORT, 'Elapsed time cannot be negative');
END;
PRAGMA user_version = 5;
