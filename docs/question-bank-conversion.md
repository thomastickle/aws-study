# Standard question-bank conversion

Source converters read external content and produce the standard schema-v2 bank.
The bank validator checks that contract; one generic importer persists it. A
converter never opens SQLite, creates sessions, or imports as a side effect.
Conversion and quizzes require no network or additional runtime dependencies.

## CloudCertPrep CLF-C02

Provide either a local CloudCertPrep checkout or a directory containing all four
`domain1.json` through `domain4.json` files. The converter supports CLF-C02 single
and multiple response questions. Domain classification comes from an explicit
mapping verified against the upstream certification registry, not keywords.

```bash
python aws-study.py convert cloudcertprep \
  --input ../cloudcertprep --cert CLF-C02 \
  --output private/generated/cloudcertprep-clf-c02.json
python aws-study.py import-json private/generated/cloudcertprep-clf-c02.json --dry-run
python aws-study.py import-json private/generated/cloudcertprep-clf-c02.json
```

Output is readable, deterministic JSON. Existing output requires `--force`;
output inside the input directory is rejected. Conversion validates the entire
corpus before publishing and identifies failures by file, domain, and record ID.
Its summary reports counts, optional metadata coverage, duplicates, and anomalies.
The Python developer helper `audit_corpus(path)` returns the same parser's audit
without writing a bank, including invalid records.

Clean checkouts use the Git SHA in snapshot identity. Copies and modified
checkouts use a deterministic snapshot digest; unrelated ancestor Git checkouts
are never used as upstream identity. Repository/certification, available revision,
content digests, update metadata, and license notice remain inspectable in JSON.
No conversion timestamp is added, so repeating a conversion is reproducible.

## Trust and verification

CloudCertPrep is `third_party`, never AWS official. A real `lastVerified` date
becomes occurrence-level `verified_at` and `verified_current`, with its year used
by existing selection rules. Undated records stay `unverified`; neither a commit
date nor the freshness ledger verifies a question. For the supplied snapshot,
four of 1,050 records carry 2026 verification dates. Use `--include-unverified`
to study the remainder, or `source-verify` after deliberately checking the source.

Manual source verification controls effective status/year on re-import, while
exact upstream occurrence dates remain available for audit. Source-level fallback
still applies to existing banks. Third-party official-status claims are rejected
on import and manual verification.

## Snapshot replacement

A different snapshot in an active family requires explicit replacement:

```bash
python aws-study.py import-json private/generated/cloudcertprep-new.json \
  --supersede-source '<previous source key>' --dry-run
python aws-study.py import-json private/generated/cloudcertprep-new.json \
  --supersede-source '<previous source key>'
```

Preview reports `superseded_sources` and changes no persisted state. Import and
retirement share one transaction. Unchanged canonical content deduplicates;
changed content receives a separate canonical identity. Old provenance, attempts,
drafts, and saved answer order remain valid. Retired snapshot occurrences cannot
make questions eligible, even with `--include-unverified`; another active source
can still support those questions. Re-importing a retired snapshot does not
reactivate it, and manual verification cannot reactivate it either.

Snapshot content/provenance cannot change under an existing identity. Conflicting
answer keys still fail, including corrections supplied by a new revision. Resolve
those conflicts explicitly; this workflow never rewrites historical grading.

## Optional schema-v2 provenance

Existing banks need no rewriting. Questions may add `explanation`, exact ISO
`verified_at`, `verification_status`, `source_classification`, and `source_metadata`.
Source objects may add `metadata` and `snapshot_family`. Metadata must be a JSON
object with finite JSON values and no attempt/session history.

Existing `classification` requires area and topic. Alternatively, source-only
classification requires an area and permits an absent/null topic. Missing tasks
are never invented. New canonical questions are seeded from supplied
classification; subsequent imports retain non-null canonical values and keep
each source's own taxonomy independently. Classification differences are not
content or answer-key conflicts.

Question explanations belong to occurrences and never become answer rationales.
Full session context includes optional provenance after grading. Compact study
prompts and pre-grading output do not gain explanations or answers.

## Attribution and private files

The audited CloudCertPrep revision uses MIT, copyright 2026 Alex Santonastaso.
Generated banks retain its notice. A full copy's `LICENSE` is read verbatim;
loose files without one use the bundled notice from the audited upstream revision.
Supply the upstream license with loose files when using another revision. Keep
downloaded/generated corpus data under ignored `private/`; public tests use
synthetic fixtures. Converter code can be published separately from corpus data.

[Back to README](../README.md)
