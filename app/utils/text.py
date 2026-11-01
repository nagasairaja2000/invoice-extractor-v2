"""Text normalisation helpers."""

from __future__ import annotations

import re

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACES = re.compile(r"[ \t]+\n")


def clean_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Normalise extracted text and truncate it to a safe budget.

    Returns ``(text, truncated)``.
    """
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _CONTROL_CHARS.sub(" ", cleaned)
    cleaned = _TRAILING_SPACES.sub("\n", cleaned)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n", cleaned).strip()

    if len(cleaned) > max_chars:
        return cleaned[:max_chars], True
    return cleaned, False
