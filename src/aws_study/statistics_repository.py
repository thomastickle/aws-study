"""Aggregate study-history queries for the statistics screen."""
from __future__ import annotations

from dataclasses import dataclass

from .repository import SQLiteRepository


@dataclass(frozen=True)
class TopicStatistics:
    """Attempt and miss counts for one weak topic."""

    topic: str
    attempts: int
    misses: int


@dataclass(frozen=True)
class StudyStatistics:
    """Certification totals and weak topics ordered by miss rate."""

    active_questions: int
    attempts: int
    correct: int
    weak_topics: tuple[TopicStatistics, ...]


class StatisticsRepository(SQLiteRepository):
    """Return database aggregates as display-independent models."""

    def for_certification(self, certification_id: int) -> StudyStatistics:
        """Count canonical active questions and all historical attempts."""
        params = {"certification_id": certification_id}
        question_count = self._conn.execute(
            """SELECT COUNT(*) FROM questions
               WHERE certification_id=:certification_id
                 AND is_active=1 AND dedup_role='canonical'""",
            params,
        ).fetchone()[0]
        totals = self._conn.execute(
            """SELECT COUNT(*) attempts, COALESCE(SUM(a.is_correct), 0) correct
               FROM attempts a JOIN questions q ON q.id=a.question_id
               WHERE q.certification_id=:certification_id""",
            params,
        ).fetchone()
        topics = self._conn.execute(
            """SELECT COALESCE(q.topic, 'General AWS') topic,
                      COUNT(a.id) attempts,
                      SUM(CASE WHEN a.is_correct=0 THEN 1 ELSE 0 END) misses
               FROM attempts a JOIN questions q ON q.id=a.question_id
               WHERE q.certification_id=:certification_id
               GROUP BY COALESCE(q.topic, 'General AWS') HAVING misses>0
               ORDER BY (1.0*misses/attempts) DESC, misses DESC""",
            params,
        ).fetchall()
        return StudyStatistics(
            question_count, totals["attempts"], totals["correct"],
            tuple(TopicStatistics(
                row["topic"], row["attempts"], row["misses"],
            ) for row in topics),
        )
