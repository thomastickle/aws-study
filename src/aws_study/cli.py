"""Command parsing, terminal output, and application composition."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .bank_schema import BankValidationError, load_bank
from .certification_repository import CertificationRepository
from .db import DEFAULT_DB, SCHEMA_VERSION, connect, init_db, open_readonly
from .import_models import ImportSummary
from .importers import import_bank, preview_bank
from .migrations import migrate_db
from .question_group_repository import QuestionGroupRepository
from .question_group_service import QuestionGroupService
from .quiz import run_quiz
from .quiz_repository import QuizRepository
from .quiz_service import QuizService
from .report_repository import ReportRepository
from .report_service import ReportService
from .reporting import (
    DEFAULT_REPORT_DIR,
    continuation_prompt,
    write_report_bundle,
)
from .source_repository import SourceRepository
from .source_service import VERIFICATION_STATUSES, SourceService
from .statistics_repository import StatisticsRepository
from .terminal import DEFAULT_WIDTH


def _db(args: argparse.Namespace) -> sqlite3.Connection:
    conn = connect(args.db)
    try:
        init_db(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def cmd_init(args: argparse.Namespace) -> None:
    """Initialize the schema and report its location."""
    with closing(_db(args)):
        pass
    print(f"Initialized {args.db}")


def cmd_cert_add(args: argparse.Namespace) -> None:
    """Save a certification definition and display its identity."""
    with closing(_db(args)) as conn:
        repository = CertificationRepository(conn)
        with repository.transaction():
            certification_id = repository.upsert(
                args.code,
                provider=args.provider,
                name=args.name,
                version=args.version,
                active_from_year=args.active_from_year,
                active_to_year=args.active_to_year,
            )
    print(f"Certification id={certification_id}: {args.provider} {args.code}")


def cmd_import(args: argparse.Namespace) -> None:
    """Import a bank and print the operation's summary."""
    if args.dry_run:
        try:
            if Path(args.db).exists():
                with closing(open_readonly(args.db)) as conn:
                    summary = preview_bank(conn, args.path)
            else:
                summary = preview_bank(None, args.path)
        except BankValidationError as error:
            summary = ImportSummary(
                source=Path(args.path).name,
                questions_seen=error.questions_seen,
                invalid_records=error.errors,
            )
        print(json.dumps(asdict(summary), indent=2))
        if summary.conflicts or summary.invalid_records:
            raise SystemExit(2)
        return
    bank = load_bank(args.path)
    with closing(_db(args)) as conn:
        summary = import_bank(conn, bank, filename=Path(args.path).name)
    print(json.dumps(asdict(summary), indent=2))


def cmd_migrate(args: argparse.Namespace) -> None:
    """Migrate a supported database into a separately verified current file."""
    print(json.dumps(migrate_db(args.source, args.dest), indent=2))


def cmd_question_group(args: argparse.Namespace) -> None:
    """Assign confirmed variants to a group that excludes co-selection."""
    with closing(_db(args)) as conn:
        cert_id = CertificationRepository(conn).require_id(
            args.cert, args.provider
        )
        QuestionGroupService(QuestionGroupRepository(conn)).assign(
            cert_id,
            args.question_ids,
            key=args.key,
        )
    print(
        f"Selection group {args.key}: questions {', '.join(map(str, sorted(set(args.question_ids))))}"
    )


def cmd_quiz(args: argparse.Namespace) -> None:
    """Run an interactive session and export its prepared report."""
    with closing(_db(args)) as conn:
        certification_id = CertificationRepository(conn).require_id(
            args.cert,
            args.provider,
        )
        service = QuizService(QuizRepository(conn))
        session_id = run_quiz(
            service,
            certification_id,
            count=args.count,
            target_year=args.year,
            mode=args.mode,
            strategy=args.strategy,
            seed=args.seed,
            include_unverified=args.include_unverified,
            width=args.width,
        )
        bundle = ReportService(ReportRepository(conn)).bundle(session_id)
    paths = write_report_bundle(bundle, args.reports)
    print(f"\nSession {session_id} saved.")
    for kind, path in paths.items():
        print(f"  {kind}: {path}")


def cmd_report(args: argparse.Namespace) -> None:
    """Regenerate an export for the requested session."""
    with closing(_db(args)) as conn:
        service = ReportService(ReportRepository(conn))
        bundle = service.bundle(service.resolve_session(args.session))
    for kind, path in write_report_bundle(bundle, args.reports).items():
        print(f"{kind}: {path}")


def cmd_prompt(args: argparse.Namespace) -> None:
    """Print a continuation prompt for the requested session."""
    with closing(_db(args)) as conn:
        service = ReportService(ReportRepository(conn))
        data = service.session_data(service.resolve_session(args.session))
    print(continuation_prompt(data))


def cmd_stats(args: argparse.Namespace) -> None:
    """Display certification totals and weak topics."""
    with closing(_db(args)) as conn:
        certification_id = CertificationRepository(conn).require_id(
            args.cert,
            args.provider,
        )
        stats = StatisticsRepository(conn).for_certification(certification_id)
    print(
        f"{args.provider} {args.cert}: "
        f"{stats.active_questions} canonical active questions"
    )
    print(f"Attempts: {stats.attempts}; correct: {stats.correct}")
    print("\nWeak topics:")
    for topic in stats.weak_topics:
        print(
            f"  {topic.area} / {topic.topic}: {topic.misses}/{topic.attempts} missed"
        )


def cmd_sources(args: argparse.Namespace) -> None:
    """Display source provenance, freshness, and question counts."""
    with closing(_db(args)) as conn:
        certification_id = CertificationRepository(conn).require_id(
            args.cert,
            args.provider,
        )
        sources = SourceRepository(conn).summaries(certification_id)
    for source in sources:
        print(
            f"{source.key}: {source.name} | {source.source_type} | "
            f"observed={source.observed_year} | "
            f"status={source.verification_status} | "
            f"verified_at={source.verified_at} | "
            f"questions={source.question_count} "
            f"({source.canonical_count} canonical)"
        )


def cmd_source_verify(args: argparse.Namespace) -> None:
    """Apply explicit source verification and display the saved year/status."""
    with closing(_db(args)) as conn:
        certification_id = CertificationRepository(conn).require_id(
            args.cert,
            args.provider,
        )
        SourceService(SourceRepository(conn)).verify(
            certification_id,
            args.cert,
            args.source_key,
            year=args.year,
            status=args.status,
        )
    print(
        f"Updated {args.source_key}: status={args.status}, "
        f"verified_year={args.year}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Define commands and defaults without opening the database."""
    p = argparse.ArgumentParser(
        prog="aws-study",
        description="SQLite-backed certification study CLI",
    )
    p.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        help="SQLite database path (default: private/aws-study.db)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init", help="Initialize the SQLite database")
    x.set_defaults(func=cmd_init)

    x = sub.add_parser(
        "cert-add",
        help="Add/update a certification definition",
    )
    x.add_argument("--provider", default="AWS")
    x.add_argument("--code", required=True)
    x.add_argument("--name")
    x.add_argument("--version")
    x.add_argument("--active-from-year", type=int)
    x.add_argument("--active-to-year", type=int)
    x.set_defaults(func=cmd_cert_add)

    x = sub.add_parser(
        "import-json",
        help="Import a curated schema-v2 question-bank JSON file",
    )
    x.add_argument("path")
    x.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and preview against a read-only database snapshot",
    )
    x.set_defaults(func=cmd_import)

    x = sub.add_parser(
        "migrate-db",
        help="Migrate a study database to the current schema in a new file",
        description=(
            f"Migrate any supported study database to the current SQLite "
            f"schema (v{SCHEMA_VERSION}) in a separate destination file. "
            "The original is preserved. Question-bank JSON has a separate version."
        ),
    )
    x.add_argument("--source", required=True)
    x.add_argument("--dest", required=True)
    x.set_defaults(func=cmd_migrate)

    x = sub.add_parser(
        "question-group", help="Keep confirmed variants out of the same quiz"
    )
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("--key", required=True, help="Curated selection group name")
    x.add_argument("question_ids", nargs="+", type=int)
    x.set_defaults(func=cmd_question_group)

    x = sub.add_parser("quiz", help="Run a random/adaptive mini exam")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("-n", "--count", type=int, default=20)
    x.add_argument(
        "--year",
        type=int,
        default=datetime.now().year,
        help="Target exam year; defaults to the current local year",
    )
    x.add_argument("--mode", choices=["exam", "study"], default="exam")
    x.add_argument(
        "--strategy",
        choices=["adaptive", "random", "weak", "new"],
        default="adaptive",
    )
    x.add_argument("--seed", type=int)
    x.add_argument("--include-unverified", action="store_true")
    x.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help="Maximum quiz text width; adapts to narrower terminals "
        "(default: %(default)s)",
    )
    x.add_argument(
        "--reports",
        default=str(DEFAULT_REPORT_DIR),
        help="Parent directory for report bundles (default: %(default)s)",
    )
    x.set_defaults(func=cmd_quiz)

    x = sub.add_parser(
        "report",
        help="Regenerate a report/prompt/context pack for a session",
    )
    x.add_argument("session", nargs="?", default="latest")
    x.add_argument(
        "--reports",
        default=str(DEFAULT_REPORT_DIR),
        help="Parent directory for report bundles (default: %(default)s)",
    )
    x.set_defaults(func=cmd_report)

    x = sub.add_parser(
        "prompt",
        help="Print the compact ChatGPT continuation prompt",
    )
    x.add_argument("session", nargs="?", default="latest")
    x.set_defaults(func=cmd_prompt)

    x = sub.add_parser(
        "stats",
        help="Show basic bank and weak-area statistics",
    )
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.set_defaults(func=cmd_stats)

    x = sub.add_parser(
        "sources",
        help="List question-bank sources and freshness metadata",
    )
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.set_defaults(func=cmd_sources)

    x = sub.add_parser(
        "source-verify",
        help=(
            "Mark a source/questions with an explicit verification year/status"
        ),
    )
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("--source-key", required=True)
    x.add_argument("--year", type=int, required=True)
    x.add_argument(
        "--status",
        default="verified_current",
        choices=VERIFICATION_STATUSES,
    )
    x.set_defaults(func=cmd_source_verify)
    return p


def main(argv: list[str] | None = None) -> None:
    """Dispatch one command and translate expected failures to CLI errors."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, sqlite3.Error, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)
