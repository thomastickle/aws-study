"""Import summaries suitable for CLI JSON and read-only previews."""

from dataclasses import dataclass, field


@dataclass
class ImportSummary:
    """Counts from a successful import or a disposable preview."""

    source: str
    questions_seen: int = 0
    new_canonical_questions: int = 0
    existing_canonical_matches: int = 0
    new_provenance_links: int = 0
    conflicts: list[str] = field(default_factory=list)
    invalid_records: list[str] = field(default_factory=list)


class ImportConflict(ValueError):
    """Conflicts discovered in a transaction that was completely rolled back."""

    def __init__(self, summary: ImportSummary):
        self.summary = summary
        super().__init__("Import conflicts: " + "; ".join(summary.conflicts))
