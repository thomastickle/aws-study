-- Preserve source taxonomy and exact upstream provenance independently.
ALTER TABLE question_sources ADD COLUMN explanation TEXT;
ALTER TABLE question_sources ADD COLUMN verified_at TEXT;
ALTER TABLE question_sources ADD COLUMN source_area TEXT;
ALTER TABLE question_sources ADD COLUMN source_topic TEXT;
ALTER TABLE question_sources ADD COLUMN metadata_json TEXT;

UPDATE question_sources SET
    source_area=(SELECT area FROM questions WHERE id=question_id),
    source_topic=(SELECT topic FROM questions WHERE id=question_id);

ALTER TABLE sources ADD COLUMN metadata_json TEXT;
ALTER TABLE sources ADD COLUMN snapshot_family TEXT;
ALTER TABLE sources ADD COLUMN snapshot_fingerprint TEXT;
ALTER TABLE sources ADD COLUMN superseded_by_source_id INTEGER
    REFERENCES sources(id);
CREATE INDEX idx_source_snapshot_family ON sources(certification_id,snapshot_family);

PRAGMA user_version = 7;
