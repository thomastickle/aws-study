-- Shared by canonical upgrades and v1 conversion after its history is copied.
INSERT INTO archived_attempts
SELECT a.* FROM attempts a JOIN sessions s ON s.id=a.session_id
WHERE s.source_kind='interactive' AND s.mode='exam' AND s.completed_at IS NULL;
INSERT INTO archived_attempt_options
SELECT ao.* FROM attempt_options ao JOIN archived_attempts a ON a.id=ao.attempt_id;
INSERT INTO session_responses(session_id,question_id,confidence,elapsed_ms,
                              first_answered_at,updated_at)
SELECT a.session_id,a.question_id,a.confidence,a.elapsed_ms,a.attempted_at,a.attempted_at
FROM attempts a JOIN archived_attempts old ON old.id=a.id;
INSERT INTO session_response_answers(session_id,question_id,answer_id)
SELECT a.session_id,a.question_id,ao.option_id
FROM attempts a JOIN archived_attempts old ON old.id=a.id
JOIN attempt_options ao ON ao.attempt_id=a.id WHERE ao.selected=1;
DELETE FROM attempt_options WHERE attempt_id IN (SELECT id FROM archived_attempts);
DELETE FROM attempts WHERE id IN (SELECT id FROM archived_attempts);
