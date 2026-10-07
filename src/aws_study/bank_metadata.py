"""Validation of portable provenance values without database dependencies."""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, TypeAlias, Union

JsonValue: TypeAlias = Union[
    None, bool, int, float, str, list["JsonValue"], dict[str, "JsonValue"]
]
JsonObject: TypeAlias = dict[str, JsonValue]

HISTORY_FIELDS = {
    "attempts",
    "session_id",
    "session_responses",
    "confidence",
    "original_confidence",
    "selected_answers",
    "selected_answer_ids",
    "elapsed_ms",
    "time_to_answer_seconds",
}


def validate_date(value: Any, field: str) -> str:
    """Require a real calendar date in the exact YYYY-MM-DD representation."""
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", value
    ):
        raise ValueError(f"{field} must be an ISO YYYY-MM-DD date")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be a valid calendar date") from error
    return value


def _json_value(value: Any, field: str) -> JsonValue:
    if value is None or type(value) in (bool, int, str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if isinstance(value, list):
        return [_json_value(item, field) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{field} keys must be strings")
        if HISTORY_FIELDS.intersection(value):
            raise ValueError(
                f"runtime/history fields do not belong in {field}"
            )
        return {key: _json_value(item, field) for key, item in value.items()}
    raise ValueError(f"{field} must contain finite JSON values")


def validate_metadata(value: Any, field: str) -> JsonObject | None:
    """Accept an optional JSON object, preserving empty objects and lists."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object or null")
    result = _json_value(value, field)
    if not isinstance(result, dict):
        raise RuntimeError("Metadata validation did not return an object")
    return result
