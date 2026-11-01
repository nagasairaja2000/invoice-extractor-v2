"""Utility helpers for parsing JSON returned by the LLM."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*")
_TRAILING_FENCE_RE = re.compile(r"\s*```\s*$")


def strip_code_fences(text: str) -> str:
    """Remove markdown code fences that models sometimes add."""
    cleaned = text.strip()
    cleaned = _FENCE_RE.sub("", cleaned)
    cleaned = _TRAILING_FENCE_RE.sub("", cleaned)
    return cleaned.strip()


def extract_json_object(text: str) -> str:
    """Extract the first balanced JSON object from a text blob.

    Useful when a model wraps JSON in prose despite instructions.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model response.")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Unbalanced JSON object in model response.")


def loads(text: str) -> Any:
    """Best-effort JSON parsing for LLM responses."""
    if not text or not text.strip():
        raise ValueError("Empty model response.")

    candidates = [text.strip(), strip_code_fences(text)]

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    return json.loads(extract_json_object(strip_code_fences(text)))
