-- Answer IDs retain identity; positions are saved independently per session.
CREATE UNIQUE INDEX idx_answer_question_identity ON answers(id, question_id);
CREATE TABLE session_answers (
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    answer_id INTEGER NOT NULL,
    display_order INTEGER NOT NULL CHECK(display_order >= 1),
    PRIMARY KEY(session_id, question_id, answer_id),
    UNIQUE(session_id, question_id, display_order),
    FOREIGN KEY(session_id, question_id)
        REFERENCES session_questions(session_id, question_id) ON DELETE CASCADE,
    FOREIGN KEY(answer_id, question_id)
        REFERENCES answers(id, question_id) ON DELETE CASCADE
);
-- Previous interactive sessions used canonical order; do not shuffle history.
INSERT INTO session_answers(session_id, question_id, answer_id, display_order)
SELECT sq.session_id, sq.question_id, a.id, a.display_order
FROM session_questions sq JOIN answers a ON a.question_id=sq.question_id;
PRAGMA user_version = 4;
