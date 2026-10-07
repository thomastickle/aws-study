"""Canonical content and provenance persistence for imports and migration."""

from __future__ import annotations

import json
from dataclasses import asdict

from .bank_schema import BankQuestion
from .fingerprints import (
    FINGERPRINT_VERSION,
    answer_key_fingerprint,
    content_fingerprint,
    normalize_match_text,
)
from .repository import SQLiteRepository


class ImportRepository(SQLiteRepository):
    """Persist validated content; never commit independently of the caller."""

    def canonical_question(
        self,
        certification_id: int,
        question: BankQuestion,
        *,
        context: str,
        preferred_id: int | None = None,
        is_active: int = 1,
        created_at: str | None = None,
    ) -> tuple[int, bool]:
        """Match content and grading; canonical classification is first-owned."""
        fingerprint = content_fingerprint(
            question.text, (a.text for a in question.answers)
        )
        key = answer_key_fingerprint(
            a.text for a in question.answers if a.correct
        )
        params = {
            "certification_id": certification_id,
            "question_text": question.text,
            "question_type": question.kind,
            "select_count": question.select_count,
            "area": question.area
            or (
                question.source_classification.area
                if question.source_classification
                else None
            ),
            "topic": question.topic
            or (
                question.source_classification.topic
                if question.source_classification
                else None
            ),
            "content_fingerprint": fingerprint,
            "answer_key_fingerprint": key,
            "fingerprint_version": FINGERPRINT_VERSION,
            "id": preferred_id,
            "is_active": is_active,
            "created_at": created_at,
        }
        row = self._conn.execute(
            """SELECT * FROM questions WHERE certification_id=:certification_id
               AND content_fingerprint=:content_fingerprint""",
            params,
        ).fetchone()
        if row is not None:
            conflicts = []
            for field in (
                "question_type",
                "select_count",
                "answer_key_fingerprint",
            ):
                existing, incoming = row[field], params[field]
                if existing != incoming:
                    if field == "answer_key_fingerprint":
                        existing = [
                            r[0]
                            for r in self._conn.execute(
                                "SELECT answer_text FROM answers "
                                "WHERE question_id=:id AND is_correct=1",
                                {"id": row["id"]},
                            )
                        ]
                        incoming = [
                            a.text for a in question.answers if a.correct
                        ]
                    conflicts.append(
                        f"{field}: existing={existing!r}, incoming={incoming!r}"
                    )
            if conflicts:
                raise ValueError(
                    f"Question {row['id']} conflict at {context}; "
                    f"stem={question.text!r}; " + "; ".join(conflicts)
                )
            self._conn.execute(
                """UPDATE questions SET area=COALESCE(area,:area),
                   topic=COALESCE(topic,:topic) WHERE id=:id""",
                {
                    "id": row["id"],
                    "area": params["area"],
                    "topic": params["topic"],
                },
            )
            return int(row["id"]), False
        cursor = self._conn.execute(
            """INSERT INTO questions(id, certification_id, question_text,
                   question_type, select_count, area, topic, content_fingerprint,
                   answer_key_fingerprint, fingerprint_version, is_active, created_at)
               VALUES (:id,:certification_id,:question_text,:question_type,
                   :select_count,:area,:topic,:content_fingerprint,
                   :answer_key_fingerprint,:fingerprint_version,:is_active,
                   COALESCE(:created_at,CURRENT_TIMESTAMP))""",
            params,
        )
        question_id = self.inserted_id(cursor)
        for order, answer in enumerate(question.answers, 1):
            self._conn.execute(
                """INSERT INTO answers(question_id,answer_text,normalized_text,
                       is_correct,display_order,rationale)
                   VALUES (:question_id,:text,:normalized_text,:correct,:order,:rationale)""",
                {
                    **asdict(answer),
                    "question_id": question_id,
                    "order": order,
                    "normalized_text": normalize_match_text(answer.text),
                },
            )
        return question_id, True

    def answer_ids(self, question_id: int) -> dict[str, int]:
        """Return normalized answer text to canonical answer ID."""
        return {
            r["normalized_text"]: r["id"]
            for r in self._conn.execute(
                "SELECT id,normalized_text FROM answers WHERE question_id=:id",
                {"id": question_id},
            )
        }

    def provenance(
        self,
        question_id: int,
        source_id: int,
        question: BankQuestion,
        *,
        order: int,
        observed_year: int | None,
        status: str | None,
        verified_year: int | None,
        valid_to_year: int | None = None,
        created_at: str | None = None,
    ) -> bool:
        """Save an occurrence and its source-specific answer order/rationales."""
        params = {
            "question_id": question_id,
            "source_id": source_id,
            "source_ref": question.source_ref,
            "source_order": order,
            "valid_from_year": observed_year,
            "valid_to_year": valid_to_year,
            "verification_status": status,
            "verified_year": verified_year,
            "created_at": created_at,
            "explanation": question.explanation,
            "verified_at": question.verified_at,
            "source_area": (
                question.source_classification.area
                if question.source_classification
                else question.area
            ),
            "source_topic": (
                question.source_classification.topic
                if question.source_classification
                else question.topic
            ),
            "metadata_json": (
                json.dumps(
                    question.source_metadata,
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                if question.source_metadata is not None
                else None
            ),
        }
        if question.source_ref is not None:
            row = self._conn.execute(
                """SELECT id,question_id FROM question_sources
                   WHERE source_id=:source_id AND source_ref=:source_ref""",
                params,
            ).fetchone()
        else:
            row = self._conn.execute(
                """SELECT id,question_id FROM question_sources WHERE
                   source_id=:source_id AND source_ref IS NULL
                   AND question_id=:question_id""",
                params,
            ).fetchone()
        if row is not None and row["question_id"] != question_id:
            raise ValueError(
                f"Source-content-change conflict: existing question {row['question_id']}, "
                f"source {source_id}, source_ref={question.source_ref!r}, "
                f"incoming question {question_id}, stem={question.text!r}"
            )
        new = row is None
        if new:
            cursor = self._conn.execute(
                """INSERT INTO question_sources(question_id,source_id,source_ref,
                       source_order,valid_from_year,valid_to_year,
                       verification_status,verified_year,created_at,
                       explanation,verified_at,source_area,source_topic,metadata_json)
                   VALUES (:question_id,:source_id,:source_ref,:source_order,
                       :valid_from_year,:valid_to_year,:verification_status,
                       :verified_year,COALESCE(:created_at,CURRENT_TIMESTAMP),
                       :explanation,:verified_at,:source_area,:source_topic,:metadata_json)""",
                params,
            )
            link_id = self.inserted_id(cursor)
        else:
            link_id = row["id"]
            self._conn.execute(
                """UPDATE question_sources SET source_order=:source_order,
                   valid_from_year=:valid_from_year,valid_to_year=:valid_to_year,
                   verification_status=:verification_status,verified_year=:verified_year,
                   explanation=:explanation,verified_at=:verified_at,
                   source_area=:source_area,source_topic=:source_topic,metadata_json=:metadata_json
                   WHERE id=:id""",
                {**params, "id": link_id},
            )
            self._conn.execute(
                "DELETE FROM question_source_answers WHERE question_source_id=:id",
                {"id": link_id},
            )
        answer_ids = self.answer_ids(question_id)
        for position, answer in enumerate(question.answers, 1):
            self._conn.execute(
                """INSERT INTO question_source_answers(question_source_id,
                       answer_id,source_order,rationale)
                   VALUES (:id,:answer_id,:order,:rationale)""",
                {
                    "id": link_id,
                    "answer_id": answer_ids[normalize_match_text(answer.text)],
                    "order": position,
                    "rationale": answer.rationale,
                },
            )
        return new
