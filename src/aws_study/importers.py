"""Validated, transactional schema-v2 imports without inferred taxonomy."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .bank_schema import Bank, load_bank
from .bank_serialization import snapshot_fingerprint
from .certification_repository import CertificationRepository
from .db import init_db
from .import_models import ImportConflict, ImportSummary
from .import_repository import ImportRepository
from .question_group_repository import QuestionGroupRepository
from .source_repository import SourceRepository


def import_bank(
    conn: sqlite3.Connection,
    bank: Bank,
    *,
    filename: str,
    supersede_source: str | None = None,
) -> ImportSummary:
    """Import a validated source atomically, collecting actionable conflicts."""
    repository = ImportRepository(conn)
    source = bank.source
    summary = ImportSummary(source.key, questions_seen=len(bank.questions))
    with repository.transaction():
        cert_id = CertificationRepository(conn).upsert(
            bank.certification.code,
            provider=bank.certification.provider,
            name=bank.certification.name,
        )
        sources = SourceRepository(conn)
        fingerprint = (
            snapshot_fingerprint(bank) if source.snapshot_family else None
        )
        try:
            old_id = sources.prepare_snapshot(
                cert_id,
                source.key,
                source_type=source.kind,
                family=source.snapshot_family,
                fingerprint=fingerprint,
                supersede_key=supersede_source,
            )
        except ValueError as error:
            summary.conflicts.append(str(error))
            raise ImportConflict(summary) from error
        source_id = sources.upsert(
            cert_id,
            source.key,
            name=source.name,
            source_type=source.kind,
            source_file=filename,
            observed_year=source.observed_year,
            verification_status=source.verification_status,
            verified_at=(
                f"{source.verified_year:04d}-01-01"
                if source.verified_year is not None
                else None
            ),
            metadata_json=(
                json.dumps(
                    source.metadata,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if source.metadata is not None
                else None
            ),
            snapshot_family=source.snapshot_family,
            snapshot_fingerprint=fingerprint,
        )
        status, year = sources.verification(source_id)
        manual = sources.verification_origin(source_id) == "manual"
        for position, question in enumerate(bank.questions, 1):
            try:
                question_id, new = repository.canonical_question(
                    cert_id,
                    question,
                    context=f"source={source.key}, source_ref={question.source_ref!r}, position={position}",
                )
                if question.selection_group is not None:
                    QuestionGroupRepository(conn).assign(
                        cert_id,
                        [question_id],
                        key=question.selection_group,
                    )
                summary.new_canonical_questions += new
                summary.existing_canonical_matches += not new
                occurrence_status = status
                occurrence_year = year
                if not manual and status != "superseded":
                    occurrence_status = question.verification_status or status
                    if question.verified_at is not None:
                        occurrence_year = int(question.verified_at[:4])
                summary.new_provenance_links += repository.provenance(
                    question_id,
                    source_id,
                    question,
                    order=position,
                    observed_year=source.observed_year,
                    status=occurrence_status,
                    verified_year=occurrence_year,
                )
            except ValueError as error:
                summary.conflicts.append(str(error))
        if summary.conflicts:
            raise ImportConflict(summary)
        if old_id is not None:
            sources.supersede(old_id, source_id)
            if supersede_source is not None:
                summary.superseded_sources.append(supersede_source)
    return summary


def import_internal_bank(
    conn: sqlite3.Connection, path: str | Path
) -> ImportSummary:
    """Accept only v2; metadata comes from the file, never legacy CLI defaults."""
    return import_bank(conn, load_bank(path), filename=Path(path).name)


def preview_bank(
    conn: sqlite3.Connection | None,
    path: str | Path,
    *,
    supersede_source: str | None = None,
) -> ImportSummary:
    """Simulate against a disposable in-memory snapshot; persisted DB is read-only."""
    bank = load_bank(path)
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    try:
        if conn is not None:
            conn.backup(scratch)
        init_db(scratch)
        try:
            return import_bank(
                scratch,
                bank,
                filename=Path(path).name,
                supersede_source=supersede_source,
            )
        except ImportConflict as error:
            return error.summary
    finally:
        scratch.close()
