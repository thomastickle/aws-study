"""Persistence for question-bank provenance and verification metadata."""
from __future__ import annotations

from dataclasses import dataclass

from .repository import SQLiteRepository


@dataclass(frozen=True)
class SourceSummary:
    """A source's provenance and question counts for display."""

    key: str
    name: str
    source_type: str
    observed_year: int | None
    verification_status: str
    verified_at: str | None
    question_count: int
    canonical_count: int


class SourceRepository(SQLiteRepository):
    """Read and write source metadata in the caller's transaction."""

    def find_id(self, certification_id: int, source_key: str) -> int | None:
        """Find a source within one certification."""
        row = self._conn.execute(
            """SELECT id FROM sources
               WHERE certification_id=:certification_id
                 AND source_key=:source_key""",
            {
                "certification_id": certification_id,
                "source_key": source_key,
            },
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def upsert(
        self,
        certification_id: int,
        source_key: str,
        *,
        name: str,
        source_type: str,
        source_file: str,
        observed_year: int | None,
        verification_status: str,
        verified_at: str | None,
    ) -> int:
        """Save import metadata while retaining the source's identity."""
        self._conn.execute(
            """INSERT INTO sources(
                   certification_id, source_key, name, source_type,
                   source_file, observed_year, verification_status,
                   verified_at)
               VALUES (:certification_id, :source_key, :name, :source_type,
                       :source_file, :observed_year, :verification_status,
                       :verified_at)
               ON CONFLICT(certification_id, source_key) DO UPDATE SET
                   observed_year=COALESCE(excluded.observed_year,
                                          sources.observed_year),
                   verification_status=excluded.verification_status,
                   verified_at=COALESCE(excluded.verified_at,
                                        sources.verified_at),
                   source_type=excluded.source_type""",
            {
                "certification_id": certification_id,
                "source_key": source_key, "name": name,
                "source_type": source_type, "source_file": source_file,
                "observed_year": observed_year,
                "verification_status": verification_status,
                "verified_at": verified_at,
            },
        )
        source_id = self.find_id(certification_id, source_key)
        assert source_id is not None
        return source_id

    def summaries(self, certification_id: int) -> tuple[SourceSummary, ...]:
        """List sources in insertion order, including empty sources."""
        rows = self._conn.execute(
            """SELECT s.source_key, s.name, s.source_type, s.observed_year,
                      s.verification_status, s.verified_at, COUNT(q.id) total,
                      SUM(CASE WHEN q.dedup_role='canonical' THEN 1 ELSE 0 END)
                      canonical
               FROM sources s LEFT JOIN questions q ON q.source_id=s.id
               WHERE s.certification_id=:certification_id
               GROUP BY s.id ORDER BY s.id""",
            {"certification_id": certification_id},
        ).fetchall()
        return tuple(SourceSummary(
            row["source_key"], row["name"], row["source_type"],
            row["observed_year"], row["verification_status"],
            row["verified_at"], row["total"], row["canonical"],
        ) for row in rows)

    def update_verification(
        self, source_id: int, *, status: str, year: int, verified_at: str,
    ) -> None:
        """Update the source and its questions as part of one transaction."""
        params = {
            "source_id": source_id, "status": status, "year": year,
            "verified_at": verified_at,
        }
        self._conn.execute(
            """UPDATE sources
               SET verification_status=:status, verified_at=:verified_at
               WHERE id=:source_id""",
            params,
        )
        self._conn.execute(
            """UPDATE questions
               SET verification_status=:status, verified_year=:year
               WHERE source_id=:source_id""",
            params,
        )
