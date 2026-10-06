"""Shared SQLite repository plumbing, without domain queries."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class SQLiteRepository:
    """Share a connection with caller-owned lifetime and transactions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @staticmethod
    def inserted_id(cursor: sqlite3.Cursor) -> int:
        """Return a row ID after INSERT, or fail explicitly if none is available."""
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite INSERT did not return a row ID")
        return cursor.lastrowid

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit the operation or roll back its writes on failure.

        Repositories participating in one operation must share this connection.
        Write methods do not commit independently. Use one transaction boundary
        per operation rather than nesting connection transaction contexts.
        """
        with self._conn:
            yield
