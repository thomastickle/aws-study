from __future__ import annotations

import sqlite3
from importlib.resources import files
from pathlib import Path

DEFAULT_DB = Path("private/aws-study.db")


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    schema = files("aws_study").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()


def get_or_create_cert(
    conn: sqlite3.Connection,
    code: str,
    *,
    provider: str = "AWS",
    name: str | None = None,
    version: str | None = None,
    active_from_year: int | None = None,
    active_to_year: int | None = None,
) -> int:
    row = conn.execute(
        "SELECT id FROM certifications WHERE provider=? AND code=?",
        (provider, code),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE certifications
               SET name=COALESCE(?, name), version=COALESCE(?, version),
                   active_from_year=COALESCE(?, active_from_year),
                   active_to_year=COALESCE(?, active_to_year)
             WHERE id=?
            """,
            (name, version, active_from_year, active_to_year, row["id"]),
        )
        conn.commit()
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO certifications(provider, code, name, version, active_from_year, active_to_year)
        VALUES (?,?,?,?,?,?)
        """,
        (provider, code, name, version, active_from_year, active_to_year),
    )
    conn.commit()
    return int(cur.lastrowid)


def cert_id(conn: sqlite3.Connection, code: str, provider: str = "AWS") -> int:
    row = conn.execute(
        "SELECT id FROM certifications WHERE provider=? AND code=?", (provider, code)
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown certification {provider} {code}. Import a bank or add the certification first.")
    return int(row["id"])
