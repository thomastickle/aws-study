"""Validated, transactional schema-v2 imports without inferred taxonomy."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .bank_schema import Bank, load_bank
from .certification_repository import CertificationRepository
from .db import init_db
from .import_models import ImportConflict, ImportSummary
from .import_repository import ImportRepository
from .question_group_repository import QuestionGroupRepository
from .source_repository import SourceRepository


def import_bank(
    conn: sqlite3.Connection, bank: Bank, *, filename: str
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
        )
        status, year = sources.verification(source_id)
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
                summary.new_provenance_links += repository.provenance(
                    question_id,
                    source_id,
                    question,
                    order=position,
                    observed_year=source.observed_year,
                    status=status,
                    verified_year=year,
                )
            except ValueError as error:
                summary.conflicts.append(str(error))
        if summary.conflicts:
            raise ImportConflict(summary)
    return summary


def import_internal_bank(
    conn: sqlite3.Connection, path: str | Path
) -> ImportSummary:
    """Accept only v2; metadata comes from the file, never legacy CLI defaults."""
    return import_bank(conn, load_bank(path), filename=Path(path).name)


def preview_bank(
    conn: sqlite3.Connection | None, path: str | Path
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
            return import_bank(scratch, bank, filename=Path(path).name)
        except ImportConflict as error:
            return error.summary
    finally:
        scratch.close()
