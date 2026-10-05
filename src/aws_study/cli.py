from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from .db import DEFAULT_DB, cert_id, connect, get_or_create_cert, init_db
from .importers import import_internal_bank
from .quiz import run_quiz
from .reporting import continuation_prompt, session_data, write_report_bundle


def _db(args):
    conn = connect(args.db)
    init_db(conn)
    return conn


def cmd_init(args):
    conn = _db(args)
    conn.close()
    print(f"Initialized {args.db}")


def cmd_cert_add(args):
    conn = _db(args)
    cid = get_or_create_cert(
        conn, args.code, provider=args.provider, name=args.name, version=args.version,
        active_from_year=args.active_from_year, active_to_year=args.active_to_year,
    )
    print(f"Certification id={cid}: {args.provider} {args.code}")


def cmd_import(args):
    conn = _db(args)
    summary = import_internal_bank(
        conn, args.path, cert_code=args.cert, provider=args.provider, cert_name=args.cert_name,
        observed_year=args.observed_year, verified_year=args.verified_year,
        verification_status=args.verification_status, source_type=args.source_type,
        import_baseline_attempts=not args.no_baseline,
    )
    print(json.dumps(summary.__dict__, indent=2))


def cmd_quiz(args):
    conn = _db(args)
    cid = cert_id(conn, args.cert, args.provider)
    sid = run_quiz(
        conn, cid, count=args.count, target_year=args.year, mode=args.mode,
        strategy=args.strategy, seed=args.seed, include_unverified=args.include_unverified,
    )
    paths = write_report_bundle(conn, sid, args.reports)
    print(f"\nSession {sid} saved.")
    for k, p in paths.items():
        print(f"  {k}: {p}")


def _resolve_session(conn: sqlite3.Connection, value: str) -> int:
    if value != "latest":
        return int(value)
    row = conn.execute(
        "SELECT id FROM sessions WHERE source_kind='interactive' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        raise ValueError("No interactive sessions found.")
    return int(row["id"])


def cmd_report(args):
    conn = _db(args)
    sid = _resolve_session(conn, args.session)
    paths = write_report_bundle(conn, sid, args.reports)
    for k, p in paths.items():
        print(f"{k}: {p}")


def cmd_prompt(args):
    conn = _db(args)
    sid = _resolve_session(conn, args.session)
    print(continuation_prompt(session_data(conn, sid)))


def cmd_stats(args):
    conn = _db(args)
    cid = cert_id(conn, args.cert, args.provider)
    q = conn.execute(
        "SELECT COUNT(*) n FROM questions WHERE certification_id=? AND is_active=1 AND dedup_role='canonical'",
        (cid,),
    ).fetchone()["n"]
    attempts = conn.execute(
        "SELECT COUNT(*) n, SUM(is_correct) c FROM attempts a JOIN questions q ON q.id=a.question_id WHERE q.certification_id=?",
        (cid,),
    ).fetchone()
    print(f"{args.provider} {args.cert}: {q} canonical active questions")
    print(f"Attempts: {attempts['n'] or 0}; correct: {attempts['c'] or 0}")
    print("\nWeak topics:")
    rows = conn.execute(
        """
        SELECT COALESCE(q.topic,'General AWS') topic, COUNT(a.id) attempts,
               SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END) misses
        FROM attempts a JOIN questions q ON q.id=a.question_id
        WHERE q.certification_id=?
        GROUP BY COALESCE(q.topic,'General AWS') HAVING misses>0
        ORDER BY (1.0*misses/attempts) DESC, misses DESC
        """,
        (cid,),
    ).fetchall()
    for r in rows:
        print(f"  {r['topic']}: {r['misses']}/{r['attempts']} missed")



def cmd_sources(args):
    conn = _db(args)
    cid = cert_id(conn, args.cert, args.provider)
    rows = conn.execute(
        """
        SELECT s.source_key, s.name, s.source_type, s.observed_year, s.verification_status,
               s.verified_at, COUNT(q.id) total,
               SUM(CASE WHEN q.dedup_role='canonical' THEN 1 ELSE 0 END) canonical
        FROM sources s LEFT JOIN questions q ON q.source_id=s.id
        WHERE s.certification_id=?
        GROUP BY s.id ORDER BY s.id
        """, (cid,)
    ).fetchall()
    for r in rows:
        print(f"{r['source_key']}: {r['name']} | {r['source_type']} | observed={r['observed_year']} | "
              f"status={r['verification_status']} | verified_at={r['verified_at']} | "
              f"questions={r['total']} ({r['canonical']} canonical)")


def cmd_source_verify(args):
    conn = _db(args)
    cid = cert_id(conn, args.cert, args.provider)
    row = conn.execute(
        "SELECT id FROM sources WHERE certification_id=? AND source_key=?",
        (cid, args.source_key),
    ).fetchone()
    if not row:
        raise ValueError(f"Unknown source key {args.source_key!r} for {args.cert}")
    sid = int(row['id'])
    verified_at = f"{args.year}-01-01"
    conn.execute(
        "UPDATE sources SET verification_status=?, verified_at=? WHERE id=?",
        (args.status, verified_at, sid),
    )
    conn.execute(
        "UPDATE questions SET verification_status=?, verified_year=? WHERE source_id=?",
        (args.status, args.year, sid),
    )
    conn.commit()
    print(f"Updated {args.source_key}: status={args.status}, verified_year={args.year}")


def build_parser():
    p = argparse.ArgumentParser(prog="aws-study", description="SQLite-backed certification study CLI")
    p.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path (default: private/aws-study.db)")
    sub = p.add_subparsers(dest="command", required=True)

    x = sub.add_parser("init", help="Initialize the SQLite database")
    x.set_defaults(func=cmd_init)

    x = sub.add_parser("cert-add", help="Add/update a certification definition")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--code", required=True)
    x.add_argument("--name")
    x.add_argument("--version")
    x.add_argument("--active-from-year", type=int)
    x.add_argument("--active-to-year", type=int)
    x.set_defaults(func=cmd_cert_add)

    x = sub.add_parser("import-json", help="Import a normalized/internal question-bank JSON file")
    x.add_argument("path")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("--cert-name")
    x.add_argument("--observed-year", type=int)
    x.add_argument("--verified-year", type=int)
    x.add_argument("--source-type", default="official")
    x.add_argument("--verification-status", default="official_current",
                   choices=["official_current","verified_current","official_older","unverified","stale","superseded"])
    x.add_argument("--no-baseline", action="store_true")
    x.set_defaults(func=cmd_import)

    x = sub.add_parser("quiz", help="Run a random/adaptive mini exam")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("-n", "--count", type=int, default=20)
    x.add_argument("--year", type=int, default=datetime.now().year, help="Target exam year; defaults to the current local year")
    x.add_argument("--mode", choices=["exam","study"], default="exam")
    x.add_argument("--strategy", choices=["adaptive","random","weak","new"], default="adaptive")
    x.add_argument("--seed", type=int)
    x.add_argument("--include-unverified", action="store_true")
    x.add_argument("--reports", default="private/reports")
    x.set_defaults(func=cmd_quiz)

    x = sub.add_parser("report", help="Regenerate a report/prompt/context pack for a session")
    x.add_argument("session", nargs="?", default="latest")
    x.add_argument("--reports", default="private/reports")
    x.set_defaults(func=cmd_report)

    x = sub.add_parser("prompt", help="Print the compact ChatGPT continuation prompt")
    x.add_argument("session", nargs="?", default="latest")
    x.set_defaults(func=cmd_prompt)

    x = sub.add_parser("stats", help="Show basic bank and weak-area statistics")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.set_defaults(func=cmd_stats)

    x = sub.add_parser("sources", help="List question-bank sources and freshness metadata")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.set_defaults(func=cmd_sources)

    x = sub.add_parser("source-verify", help="Mark a source/questions with an explicit verification year/status")
    x.add_argument("--provider", default="AWS")
    x.add_argument("--cert", required=True)
    x.add_argument("--source-key", required=True)
    x.add_argument("--year", type=int, required=True)
    x.add_argument("--status", default="verified_current",
                   choices=["official_current","verified_current","official_older","unverified","stale","superseded"])
    x.set_defaults(func=cmd_source_verify)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (ValueError, sqlite3.Error, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        raise SystemExit(2)
