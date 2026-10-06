"""Source verification rules and transaction ownership."""
from __future__ import annotations

from .source_repository import SourceRepository

VERIFICATION_STATUSES = (
    "official_current", "verified_current", "official_older",
    "unverified", "stale", "superseded",
)


class SourceService:
    """Coordinate verification of a source and all its questions."""

    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    def verify(
        self, certification_id: int, certification_code: str, source_key: str,
        *, year: int, status: str,
    ) -> None:
        """Atomically apply the requested year/status to an existing source."""
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"Unknown verification status {status!r}")
        with self._repository.transaction():
            source_id = self._repository.find_id(
                certification_id, source_key,
            )
            if source_id is None:
                raise ValueError(
                    f"Unknown source key {source_key!r} "
                    f"for {certification_code}"
                )
            self._repository.update_verification(
                source_id, status=status, year=year,
                verified_at=f"{year}-01-01",
            )
