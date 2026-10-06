"""SQLite connections and explicit database schema versions."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from importlib.resources import files
from pathlib import Path

DEFAULT_DB = Path("private/aws-study.db")
SCHEMA_VERSION = 3


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing database without creating it or changing pragmas."""
    conn = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True
    )
    conn.row_factory = sqlite3.Row
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    """Detect empty, legacy v1, or supported versioned study databases."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if version == 0 and not tables:
        return 0
    if (
        version in (0, 1)
        and {"questions", "options", "attempts", "sources"} <= tables
    ):
        columns = {r[1] for r in conn.execute("PRAGMA table_info(questions)")}
        if {"source_id", "external_key", "content_hash"} <= columns:
            return 1
    required = {
        "certifications": {"id", "provider", "code"},
        "sources": {
            "id",
            "certification_id",
            "source_key",
            "verification_origin",
            "verified_at",
        },
        "questions": {
            "id",
            "certification_id",
            "question_text",
            "question_type",
            "select_count",
            "area",
            "topic",
            "content_fingerprint",
            "answer_key_fingerprint",
            "fingerprint_version",
            "is_active",
        },
        "answers": {
            "id",
            "question_id",
            "answer_text",
            "normalized_text",
            "is_correct",
            "display_order",
            "rationale",
        },
        "question_sources": {
            "id",
            "question_id",
            "source_id",
            "source_ref",
            "source_order",
            "valid_from_year",
            "valid_to_year",
            "verification_status",
            "verified_year",
        },
        "question_source_answers": {
            "question_source_id",
            "answer_id",
            "source_order",
            "rationale",
        },
        "sessions": {"id", "certification_id", "source_kind"},
        "session_questions": {"session_id", "question_id", "position"},
        "attempts": {
            "id",
            "session_id",
            "question_id",
            "is_correct",
            "confidence",
            "elapsed_ms",
        },
        "attempt_options": {"attempt_id", "option_id", "selected"},
        "review_notes": {"id", "question_id", "certification_id", "note_text"},
    }
    if version in (2, 3) and required.keys() <= tables:
        for table, expected in required.items():
            columns = {
                r[1] for r in conn.execute(f"PRAGMA table_info({table})")
            }
            if not expected <= columns:
                raise ValueError(
                    f"Incomplete v2 schema: missing columns in {table}"
                )
        if version == 3:
            columns = {
                r[1]
                for r in conn.execute(
                    "PRAGMA table_info(question_selection_groups)"
                )
            }
            if not {"question_id", "group_key"} <= columns:
                raise ValueError(
                    "Incomplete v3 schema: missing selection groups"
                )
        return version
    raise ValueError(f"Unsupported database schema (user_version={version})")


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open a caller-owned connection; reject legacy before changing its journal."""
    p = Path(path)
    if p.exists():
        with closing(open_readonly(p)) as probe:
            version = schema_version(probe)
        if version == 1:
            raise ValueError(
                "Legacy database: run migrate-v1-to-v2 --source "
                f"{p} --dest private/aws-study-v2.db"
            )
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        conn.close()
        raise
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize new databases; never pretend CREATE IF NOT EXISTS migrates."""
    version = schema_version(conn)
    if version == SCHEMA_VERSION:
        return
    if version == 1:
        raise ValueError(
            "Legacy database: run migrate-v1-to-v2 into a new file"
        )
    scripts = ["schema_v3.sql"]
    if version == 0:
        scripts.insert(0, "schema.sql")
    schema = "\n".join(
        files("aws_study").joinpath(name).read_text(encoding="utf-8")
        for name in scripts
    )
    # SQLite cannot enable foreign keys once a transaction has started.
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript("BEGIN;\n" + schema + "\nCOMMIT;")
    except BaseException:
        conn.rollback()
        raise
