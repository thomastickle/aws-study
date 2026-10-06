"""Source verification rules and transaction ownership."""

from __future__ import annotations

from .source_repository import SourceRepository

from .bank_schema import VERIFICATION_STATUSES


class SourceService:
    """Coordinate verification of a source and its provenance."""

    def __init__(self, repository: SourceRepository) -> None:
        self._repository = repository

    def verify(
        self,
        certification_id: int,
        certification_code: str,
        source_key: str,
        *,
        year: int,
        status: str,
    ) -> None:
        """Atomically apply the requested year/status to an existing source."""
        if not 1 <= year <= 9999:
            raise ValueError("Verification year must be between 1 and 9999")
        if status not in VERIFICATION_STATUSES:
            raise ValueError(f"Unknown verification status {status!r}")
        with self._repository.transaction():
            source_id = self._repository.find_id(
                certification_id,
                source_key,
            )
            if source_id is None:
                raise ValueError(
                    f"Unknown source key {source_key!r} "
                    f"for {certification_code}"
                )
            self._repository.update_verification(
                source_id,
                status=status,
                year=year,
                verified_at=f"{year}-01-01",
            )
