"""Source adapters produce standard banks; persistence stays in the importer."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..bank_schema import Bank
from ..bank_serialization import bank_json


def write_bank(bank: Bank, output: str | Path, *, force: bool = False) -> None:
    """Publish complete JSON atomically, refusing accidental overwrites."""
    output = Path(output)
    if not force and (output.exists() or output.is_symlink()):
        raise ValueError(f"Output already exists: {output}; use --force")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=".aws-study-bank-", dir=output.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as stream:
            stream.write(bank_json(bank))
        if force:
            os.replace(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
