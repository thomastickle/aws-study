"""Persistence for certification definitions."""

from __future__ import annotations

from .repository import SQLiteRepository


class CertificationRepository(SQLiteRepository):
    """Look up and update certifications without committing independently."""

    def require_id(self, code: str, provider: str = "AWS") -> int:
        """Resolve a certification or explain how to add a missing one."""
        row = self._conn.execute(
            """SELECT id FROM certifications
               WHERE provider=:provider AND code=:code""",
            {"provider": provider, "code": code},
        ).fetchone()
        if row is None:
            raise ValueError(
                f"Unknown certification {provider} {code}. "
                "Import a bank or add the certification first."
            )
        return int(row["id"])

    def upsert(
        self,
        code: str,
        *,
        provider: str = "AWS",
        name: str | None = None,
        version: str | None = None,
        active_from_year: int | None = None,
        active_to_year: int | None = None,
    ) -> int:
        """Save metadata, preserving stored values for omitted fields."""
        params = {
            "provider": provider,
            "code": code,
            "name": name,
            "version": version,
            "active_from_year": active_from_year,
            "active_to_year": active_to_year,
        }
        self._conn.execute(
            """INSERT INTO certifications(
                   provider, code, name, version, active_from_year,
                   active_to_year)
               VALUES (:provider, :code, :name, :version, :active_from_year,
                       :active_to_year)
               ON CONFLICT(provider, code) DO UPDATE SET
                   name=COALESCE(excluded.name, certifications.name),
                   version=COALESCE(excluded.version, certifications.version),
                   active_from_year=COALESCE(excluded.active_from_year,
                                             certifications.active_from_year),
                   active_to_year=COALESCE(excluded.active_to_year,
                                           certifications.active_to_year)""",
            params,
        )
        return self.require_id(code, provider)
