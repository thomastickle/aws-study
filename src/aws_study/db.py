"""SQLite connections and schema initialization."""
from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

DEFAULT_DB = Path("private/aws-study.db")


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open a caller-owned connection with named rows and foreign keys."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create missing schema objects on a newly opened connection."""
    schema = files("aws_study").joinpath("schema.sql").read_text(
        encoding="utf-8",
    )
    conn.executescript(schema)
    conn.commit()
