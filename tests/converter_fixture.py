"""Synthetic CloudCertPrep input; never copy the private upstream corpus."""

import json
from pathlib import Path


def external_question(domain=1, *, multi=False, five=False):
    labels = "ABCDE" if five else "ABCD"
    return {
        "id": f"q{domain:03d}",
        "question": f"Synthetic question for domain {domain}?",
        "options": {label: f"Synthetic option {label}" for label in labels},
        "answer": ["A", "C"] if multi else "A",
        "isMultiAnswer": multi,
        "explanation": "Synthetic source explanation.\nPreserve this newline.",
    }


def write_corpus(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for domain in range(1, 5):
        (directory / f"domain{domain}.json").write_text(
            json.dumps([external_question(domain)], indent=2) + "\n",
            encoding="utf-8",
        )


def replace_domain(directory: Path, domain: int, records):
    (directory / f"domain{domain}.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
