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
        metadata_json: str | None = None,
        snapshot_family: str | None = None,
        snapshot_fingerprint: str | None = None,
    ) -> int:
        """Save import metadata while retaining the source's identity."""
        self._conn.execute(
            """INSERT INTO sources(
                   certification_id, source_key, name, source_type,
                   source_file, observed_year, verification_status,
                   verified_at,metadata_json,snapshot_family,snapshot_fingerprint)
               VALUES (:certification_id, :source_key, :name, :source_type,
                       :source_file, :observed_year, :verification_status,
                       :verified_at,:metadata_json,:snapshot_family,:snapshot_fingerprint)
               ON CONFLICT(certification_id, source_key) DO UPDATE SET
                   observed_year=COALESCE(excluded.observed_year,
                                          sources.observed_year),
                   verification_status=CASE WHEN sources.superseded_by_source_id IS NOT NULL
                       THEN 'superseded' WHEN sources.verification_origin='manual'
                       THEN sources.verification_status ELSE excluded.verification_status END,
                   verified_at=CASE WHEN sources.verification_origin='manual'
                       THEN sources.verified_at ELSE excluded.verified_at END,
                   name=excluded.name, source_file=excluded.source_file,
                   source_type=excluded.source_type,
                   metadata_json=excluded.metadata_json,
                   snapshot_family=excluded.snapshot_family,
                   snapshot_fingerprint=excluded.snapshot_fingerprint""",
            {
                "certification_id": certification_id,
                "source_key": source_key,
                "name": name,
                "source_type": source_type,
                "source_file": source_file,
                "observed_year": observed_year,
                "verification_status": verification_status,
                "verified_at": verified_at,
                "metadata_json": metadata_json,
                "snapshot_family": snapshot_family,
                "snapshot_fingerprint": snapshot_fingerprint,
            },
        )
        source_id = self.find_id(certification_id, source_key)
        if source_id is None:
            raise RuntimeError(
                f"Source {source_key!r} could not be read after upsert"
            )
        return source_id

    def summaries(self, certification_id: int) -> tuple[SourceSummary, ...]:
        """List sources in insertion order, including empty sources."""
        rows = self._conn.execute(
            """SELECT s.source_key, s.name, s.source_type, s.observed_year,
                      s.verification_status, s.verified_at, COUNT(q.id) total,
                      COUNT(DISTINCT q.question_id) canonical
               FROM sources s LEFT JOIN question_sources q ON q.source_id=s.id
               WHERE s.certification_id=:certification_id
               GROUP BY s.id ORDER BY s.id""",
            {"certification_id": certification_id},
        ).fetchall()
        return tuple(
            SourceSummary(
                row["source_key"],
                row["name"],
                row["source_type"],
                row["observed_year"],
                row["verification_status"],
                row["verified_at"],
                row["total"],
                row["canonical"],
            )
            for row in rows
        )

    def update_verification(
        self,
        source_id: int,
        *,
        status: str,
        year: int,
        verified_at: str,
    ) -> None:
        """Update the source and its provenance as part of one transaction."""
        row = self._conn.execute(
            "SELECT source_type,superseded_by_source_id FROM sources WHERE id=:id",
            {"id": source_id},
        ).fetchone()
        if row["superseded_by_source_id"] is not None:
            raise ValueError(
                "A superseded snapshot cannot be reactivated by verification"
            )
        if row["source_type"] == "third_party" and status in (
            "official_current",
            "official_older",
        ):
            raise ValueError(
                "third_party sources cannot claim official status"
            )
        params = {
            "source_id": source_id,
            "status": status,
            "year": year,
            "verified_at": verified_at,
        }
        self._conn.execute(
            """UPDATE sources
               SET verification_status=:status, verified_at=:verified_at,
                   verification_origin='manual'
               WHERE id=:source_id""",
            params,
        )
        self._conn.execute(
            """UPDATE question_sources
               SET verification_status=:status, verified_year=:year
               WHERE source_id=:source_id""",
            params,
        )

    def verification(self, source_id: int) -> tuple[str, int | None]:
        """Return effective source verification, including explicit overrides."""
        row = self._conn.execute(
            "SELECT verification_status,verified_at FROM sources WHERE id=:id",
            {"id": source_id},
        ).fetchone()
        return row["verification_status"], (
            int(row["verified_at"][:4]) if row["verified_at"] else None
        )

    def verification_origin(self, source_id: int) -> str:
        """Distinguish a manual override from imported occurrence verification."""
        return self._conn.execute(
            "SELECT verification_origin FROM sources WHERE id=:id",
            {"id": source_id},
        ).fetchone()[0]

    def prepare_snapshot(
        self,
        certification_id: int,
        key: str,
        *,
        source_type: str,
        family: str | None,
        fingerprint: str | None,
        supersede_key: str | None,
    ) -> int | None:
        """Check immutable identity and explicit replacement before any writes.

        An old snapshot may be re-imported for audit, but never reactivated.
        Repeating an already completed replacement is harmless.
        """
        rows = self._conn.execute(
            "SELECT * FROM sources WHERE certification_id=:cert_id",
            {"cert_id": certification_id},
        ).fetchall()
        existing = next((r for r in rows if r["source_key"] == key), None)
        if existing is not None and existing["source_type"] != source_type:
            raise ValueError(
                f"Source type cannot change under existing key {key!r}"
            )
        if existing is not None and (
            existing["snapshot_family"] != family
            or existing["snapshot_fingerprint"] != fingerprint
        ):
            raise ValueError(f"Immutable snapshot conflict for source {key!r}")
        if family is None:
            if supersede_key is not None:
                raise ValueError("--supersede-source requires a snapshot bank")
            return None
        if (
            existing is not None
            and existing["superseded_by_source_id"] is not None
        ):
            if supersede_key is not None:
                raise ValueError(
                    "A superseded snapshot cannot replace another source"
                )
            return None
        active = [
            r
            for r in rows
            if r["snapshot_family"] == family
            and r["superseded_by_source_id"] is None
            and r["source_key"] != key
        ]
        if supersede_key is None:
            if active:
                raise ValueError(
                    f"Active snapshot {active[0]['source_key']!r} exists; "
                    "use --supersede-source to replace it"
                )
            return None
        old = next((r for r in rows if r["source_key"] == supersede_key), None)
        if (
            old is None
            or old["snapshot_family"] != family
            or supersede_key == key
        ):
            raise ValueError(
                "Replacement must name a different snapshot in the same certification and family"
            )
        incoming_id = existing["id"] if existing is not None else None
        if (
            old["superseded_by_source_id"] is not None
            and old["superseded_by_source_id"] != incoming_id
        ):
            raise ValueError(
                "The named snapshot was already superseded by another source"
            )
        if any(r["id"] != old["id"] for r in active):
            raise ValueError(
                "Another active snapshot in this family must be resolved first"
            )
        return int(old["id"])

    def supersede(self, old_id: int, new_id: int) -> None:
        """Retire provenance without deleting content, dates, or manual origin."""
        self._conn.execute(
            """UPDATE sources SET verification_status='superseded',
               superseded_by_source_id=:new_id WHERE id=:old_id""",
            {"old_id": old_id, "new_id": new_id},
        )
        self._conn.execute(
            "UPDATE question_sources SET verification_status='superseded' WHERE source_id=:id",
            {"id": old_id},
        )
