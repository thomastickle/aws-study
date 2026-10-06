"""Shared SQLite repository plumbing, without domain queries."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class SQLiteRepository:
    """Share a connection with caller-owned lifetime and transactions."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit the operation or roll back its writes on failure.

        Repositories participating in one operation must share this connection.
        Write methods do not commit independently. Use one transaction boundary
        per operation rather than nesting connection transaction contexts.
        """
        with self._conn:
            yield
