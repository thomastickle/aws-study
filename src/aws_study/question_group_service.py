"""Manage user-confirmed variant groups without merging historical records."""

from __future__ import annotations

from collections.abc import Sequence

from .question_group_repository import QuestionGroupRepository


class QuestionGroupService:
    """Own transactions for explicit selection-group edits."""

    def __init__(self, repository: QuestionGroupRepository) -> None:
        self._repository = repository

    def assign(
        self, certification_id: int, question_ids: Sequence[int], *, key: str
    ) -> None:
        """Put questions in a named group; a quiz may select at most one."""
        with self._repository.transaction():
            self._repository.assign(
                certification_id,
                question_ids,
                key=key,
                replace_existing=True,
            )
