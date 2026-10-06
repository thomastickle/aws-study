"""Explicit grouping of equivalent variants for quiz selection."""

from __future__ import annotations

from collections.abc import Sequence

from .repository import SQLiteRepository


class QuestionGroupRepository(SQLiteRepository):
    """Keep grouping separate from immutable content and attempt history."""

    def assign(
        self,
        certification_id: int,
        question_ids: Sequence[int],
        *,
        key: str,
        replace_existing: bool = False,
    ) -> None:
        """Assign a curated key, validating all membership before any writes.

        Group keys are scoped by the candidate's certification. Normal import
        rejects contradictory group metadata; an explicit command can edit it.
        """
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Selection group key must be nonempty text")
        ids = sorted(set(question_ids))
        if not ids:
            raise ValueError("At least one question ID is required")
        params = {f"q{index}": qid for index, qid in enumerate(ids)}
        binds = ",".join(f":{name}" for name in params)
        rows = self._conn.execute(
            f"""SELECT q.id,g.group_key FROM questions q
                LEFT JOIN question_selection_groups g ON g.question_id=q.id
                WHERE q.certification_id=:certification_id AND q.id IN ({binds})""",
            {**params, "certification_id": certification_id},
        ).fetchall()
        missing = set(ids) - {row["id"] for row in rows}
        if missing:
            raise ValueError(
                f"Unknown question IDs for certification: {sorted(missing)}"
            )
        for row in rows:
            if not replace_existing and row["group_key"] not in (None, key):
                raise ValueError(
                    f"Question {row['id']} selection_group conflict: "
                    f"existing={row['group_key']!r}, incoming={key!r}"
                )
        self._conn.executemany(
            """INSERT INTO question_selection_groups(question_id,group_key)
               VALUES (:question_id,:key) ON CONFLICT(question_id)
               DO UPDATE SET group_key=excluded.group_key""",
            ({"question_id": qid, "key": key} for qid in ids),
        )
