"""Offline CLF-C02 conversion and audit using one external-format parser."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..bank_metadata import JsonObject, validate_date
from ..bank_schema import (
    Bank,
    BankQuestion,
    BankValidationError,
    CertificationSpec,
    SourceSpec,
    validate_bank,
    validate_question,
)
from ..bank_serialization import bank_mapping
from ..fingerprints import content_fingerprint, normalize_match_text

UPSTREAM = "https://github.com/nastaso/cloudcertprep"
FAMILY = "cloudcertprep-clf-c02"
# Explicit upstream registry mapping, inspected at revision 9367b00d9f5b.
DOMAINS = {
    1: "Cloud Concepts",
    2: "Security & Compliance",
    3: "Cloud Technology & Services",
    4: "Billing, Pricing & Support",
}
TASK_LIMITS = {1: 4, 2: 4, 3: 8, 4: 3}


@dataclass(frozen=True)
class CorpusAudit:
    """Counts and anomalies from the same records the converter validates."""

    total_questions: int
    questions_per_domain: dict[str, int]
    single_select: int
    multi_select: int
    option_counts: dict[int, int]
    minimum_options: int
    maximum_options: int
    with_explanation: int
    without_explanation: int
    with_last_verified: int
    without_last_verified: int
    last_verified_range: tuple[str, str] | None
    with_task_statement: int
    without_task_statement: int
    services_present: int
    services_absent: int
    services_nonempty: int
    unique_task_statements: tuple[str, ...]
    unique_services: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    duplicate_stems: tuple[tuple[str, ...], ...]
    duplicate_contents: tuple[tuple[str, ...], ...]
    same_stem_variants: tuple[tuple[str, ...], ...]
    invalid_records: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ConversionResult:
    """A validated standard bank and its complete source audit."""

    bank: Bank
    audit: CorpusAudit


def convert_record(record: Any, domain: int) -> BankQuestion:
    """Pure mapping; never repair unsupported or inconsistent source records."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown CLF-C02 domain {domain}")
    if not isinstance(record, dict):
        raise ValueError("record must be an object")
    ref = record.get("id")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("id must be nonempty text")
    if "domainId" in record and (
        type(record["domainId"]) is not int or record["domainId"] != domain
    ):
        raise ValueError("domainId disagrees with the domain file")
    multi = record.get("isMultiAnswer")
    if type(multi) is not bool:
        raise ValueError("isMultiAnswer must be boolean")
    kind = record.get("type", "multi" if multi else "single")
    if kind not in ("single", "multi") or any(
        field in record
        for field in ("correctOrder", "targets", "correctMatches")
    ):
        raise ValueError(f"unsupported question type {kind!r}")
    if (kind == "multi") != multi:
        raise ValueError("type disagrees with isMultiAnswer")
    options = record.get("options")
    if not isinstance(options, dict) or len(options) < 2:
        raise ValueError("options must be an object with at least two choices")
    if any(label not in ("A", "B", "C", "D", "E") for label in options):
        raise ValueError("option labels must be A through E")
    answer = record.get("answer")
    if multi:
        if not isinstance(answer, list) or len(answer) < 2:
            raise ValueError(
                "multi answer must be a list of at least two labels"
            )
        labels = answer
    else:
        if not isinstance(answer, str):
            raise ValueError(
                "single answer must be one label; isMultiAnswer disagrees"
            )
        labels = [answer]
    if any(
        not isinstance(label, str) or label not in options for label in labels
    ):
        raise ValueError("answer label not found in options")
    if len(set(labels)) != len(labels):
        raise ValueError("duplicate correct answer labels")
    metadata: JsonObject = {
        "domain_id": domain,
        "option_labels": list(options),
    }
    task = record.get("taskStatement")
    if "taskStatement" in record:
        if not isinstance(task, str) or not re.fullmatch(r"[1-4]\.\d+", task):
            raise ValueError("taskStatement must be a domain.task identifier")
        task_domain, task_number = map(int, task.split("."))
        if (
            task_domain != domain
            or not 1 <= task_number <= TASK_LIMITS[domain]
        ):
            raise ValueError(
                "taskStatement is outside this domain's task range"
            )
        metadata["task_statement"] = task
    if "services" in record:
        services = record["services"]
        if not isinstance(services, list) or any(
            not isinstance(service, str) or not service.strip()
            for service in services
        ):
            raise ValueError("services must be a list of nonempty strings")
        metadata["services"] = services
    verified_at = None
    if "lastVerified" in record:
        verified_at = validate_date(record["lastVerified"], "lastVerified")
    known = {
        "id",
        "question",
        "options",
        "answer",
        "isMultiAnswer",
        "type",
        "domainId",
        "explanation",
        "taskStatement",
        "services",
        "lastVerified",
    }
    extra = {key: value for key, value in record.items() if key not in known}
    if extra:
        metadata["upstream_fields"] = extra
    return validate_question(
        {
            "source_ref": ref,
            "question": record.get("question"),
            "type": "multi_select" if multi else "single_select",
            "select_count": len(labels),
            "answers": [
                {"text": text, "correct": label in labels}
                for label, text in options.items()
            ],
            "explanation": record.get("explanation"),
            "verified_at": verified_at,
            "verification_status": "verified_current"
            if verified_at
            else "unverified",
            "source_classification": {"area": DOMAINS[domain], "topic": task},
            "source_metadata": metadata,
        }
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _audit(
    questions: list[tuple[str, BankQuestion]],
    counts: dict[str, int],
    ids: Counter[str],
    errors: list[str],
    warnings: list[str],
) -> CorpusAudit:
    stems: dict[str, list[str]] = defaultdict(list)
    contents: dict[str, list[str]] = defaultdict(list)
    stem_contents: dict[str, set[str]] = defaultdict(set)
    dates: list[str] = []
    tasks: set[str] = set()
    services: set[str] = set()
    present = nonempty = explanations = 0
    option_counts: Counter[int] = Counter()
    for filename, question in questions:
        ref = f"{filename}:{question.source_ref}"
        stem = normalize_match_text(question.text)
        fingerprint = content_fingerprint(
            question.text, (a.text for a in question.answers)
        )
        stems[stem].append(ref)
        contents[fingerprint].append(ref)
        stem_contents[stem].add(fingerprint)
        option_counts[len(question.answers)] += 1
        explanations += bool(question.explanation)
        if question.verified_at:
            dates.append(question.verified_at)
        metadata = question.source_metadata or {}
        task = metadata.get("task_statement")
        if isinstance(task, str):
            tasks.add(task)
        service_list = metadata.get("services")
        if isinstance(service_list, list):
            present += 1
            nonempty += bool(service_list)
            services.update(str(s) for s in service_list)
    total = sum(counts.values())
    task_count = sum(
        q.source_classification is not None
        and q.source_classification.topic is not None
        for _, q in questions
    )
    return CorpusAudit(
        total,
        counts,
        sum(q.kind == "single_select" for _, q in questions),
        sum(q.kind == "multi_select" for _, q in questions),
        dict(sorted(option_counts.items())),
        min(option_counts, default=0),
        max(option_counts, default=0),
        explanations,
        total - explanations,
        len(dates),
        total - len(dates),
        (min(dates), max(dates)) if dates else None,
        task_count,
        total - task_count,
        present,
        total - present,
        nonempty,
        tuple(sorted(tasks)),
        tuple(sorted(services)),
        tuple(sorted(ref for ref, count in ids.items() if count > 1)),
        tuple(tuple(refs) for refs in stems.values() if len(refs) > 1),
        tuple(tuple(refs) for refs in contents.values() if len(refs) > 1),
        tuple(
            tuple(stems[stem])
            for stem, variants in stem_contents.items()
            if len(variants) > 1
        ),
        tuple(errors),
        tuple(warnings),
    )


def _read_corpus(
    directory: Path,
) -> tuple[list[BankQuestion], CorpusAudit, str]:
    questions, errors, warnings = [], [], []
    counts: dict[str, int] = {}
    ids: Counter[str] = Counter()
    digest = hashlib.sha256()
    for domain in DOMAINS:
        path = directory / f"domain{domain}.json"
        try:
            raw = path.read_bytes()
            # Git can check out the same JSON with LF or CRLF on different OSes.
            digest.update(
                path.name.encode()
                + b"\0"
                + raw.replace(b"\r\n", b"\n")
                + b"\0"
            )
            data = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(data, list) or not data:
                raise ValueError(
                    "domain must contain a nonempty question array"
                )
        except (OSError, ValueError) as error:
            errors.append(f"{path.name}: {error}")
            continue
        counts[path.name] = len(data)
        for position, record in enumerate(data, 1):
            ref = record.get("id") if isinstance(record, dict) else None
            if isinstance(ref, str):
                ids[ref] += 1
                if ids[ref] > 1:
                    errors.append(
                        f"{path.name}, domain={domain}, id={ref!r}: duplicate source ID"
                    )
                if not re.fullmatch(r"q\d+", ref):
                    warnings.append(
                        f"{path.name}: unusual source ID {ref!r}; preserved without reclassification"
                    )
            try:
                questions.append((path.name, convert_record(record, domain)))
            except ValueError as error:
                errors.append(
                    f"{path.name}, domain={domain}, id={ref!r}, position={position}: {error}"
                )
    audit = _audit(questions, counts, ids, errors, warnings)
    return [question for _, question in questions], audit, digest.hexdigest()


def _layout(input_path: str | Path) -> tuple[Path, Path | None]:
    root = Path(input_path).resolve()
    full = root / "src/data/clf-c02"
    return (full, root) if full.is_dir() else (root, None)


def audit_corpus(input_path: str | Path) -> CorpusAudit:
    """Developer audit including invalid records; use the production parser."""
    directory, _ = _layout(input_path)
    return _read_corpus(directory)[1]


def _git_revision(
    root: Path | None, relevant: list[Path]
) -> tuple[str | None, bool]:
    if root is None:
        return None, False

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.strip()

    revision = None
    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
            return None, False
        revision = git("rev-parse", "HEAD")
        for path in relevant:
            relative = path.relative_to(root).as_posix()
            committed = git("rev-parse", f"HEAD:{relative}")
            # Apply Git's clean filters, including attributes and core.autocrlf.
            current = git("hash-object", f"--path={relative}", str(path))
            if committed != current:
                return revision, False
        return revision, True
    except (OSError, subprocess.CalledProcessError):
        return revision, False


def convert_corpus(
    input_path: str | Path, *, cert: str = "CLF-C02"
) -> ConversionResult:
    """Convert a complete local snapshot without network access or source writes."""
    if cert != "CLF-C02":
        raise ValueError(
            "CloudCertPrep conversion currently supports CLF-C02 only"
        )
    directory, root = _layout(input_path)
    questions, audit, corpus_digest = _read_corpus(directory)
    if audit.invalid_records:
        raise BankValidationError(
            list(audit.invalid_records), audit.total_questions
        )
    license_path = (root or directory) / "LICENSE"
    notice = (
        license_path.read_text(encoding="utf-8")
        if license_path.is_file()
        else files(__package__)
        .joinpath("cloudcertprep.LICENSE")
        .read_text(encoding="utf-8")
    )
    metadata: JsonObject = {
        "upstream_repository": UPSTREAM,
        "upstream_certification": cert,
        "corpus_digest": corpus_digest,
        "license_notice": notice,
    }
    relevant = [directory / f"domain{domain}.json" for domain in DOMAINS]
    if license_path.is_file():
        relevant.append(license_path)
    ledger = (
        (root / "src/data/bank-lastmod.json")
        if root
        else directory / "bank-lastmod.json"
    )
    if ledger.is_file():
        try:
            data = json.loads(
                ledger.read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
            )
            entry = data["certs"]["clf-c02"]
            validate_date(entry["lastmod"], "bank-lastmod.lastmod")
            if not isinstance(entry["contentHash"], str) or not re.fullmatch(
                r"[0-9a-f]{64}", entry["contentHash"]
            ):
                raise ValueError(
                    "bank-lastmod.contentHash must be a SHA-256 digest"
                )
            metadata["bank_lastmod"] = entry
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(
                f"{ledger}: malformed freshness ledger: {error}"
            ) from error
        relevant.append(ledger)
    revision, matches_revision = _git_revision(root, relevant)
    metadata["upstream_revision"] = revision if matches_revision else None
    metadata["checkout_revision"] = revision
    metadata["working_tree_modified"] = (
        not matches_revision if revision else None
    )
    snapshot_digest = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    metadata["snapshot_digest"] = snapshot_digest
    identity = revision if matches_revision else f"sha256-{snapshot_digest}"
    # Source defaults never promote the undated majority to verified status.
    bank = Bank(
        CertificationSpec("AWS", cert, "AWS Certified Cloud Practitioner"),
        SourceSpec(
            f"{FAMILY}@{identity}",
            "CloudCertPrep CLF-C02",
            "third_party",
            None,
            None,
            "unverified",
            metadata,
            FAMILY,
        ),
        tuple(questions),
    )
    return ConversionResult(validate_bank(bank_mapping(bank)), audit)
