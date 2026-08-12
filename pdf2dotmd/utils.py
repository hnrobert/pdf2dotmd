"""Utility helpers for markdown generation."""

from __future__ import annotations

import re
from typing import Iterable


def escape_markdown(text: str) -> str:
    """Escape markdown control characters in plain text content."""
    if not text:
        return ""

    escaped = text.replace("\\", "\\\\")
    for ch in ["`", "*", "_", "{", "}", "[", "]", "<", ">", "|"]:
        escaped = escaped.replace(ch, f"\\{ch}")
    return escaped


def clean_markdown_content(lines: Iterable[str]) -> str:
    """Join markdown lines and collapse repeated blank lines."""
    text = "\n".join(lines).rstrip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


def parse_page_range(page_spec: str, total_pages: int) -> list[int]:
    """Parse a page range string like '1-5,8,10-12' into 0-based page indices.

    Shared by every backend so page selection behaves identically regardless
    of the conversion engine.
    """
    indices: list[int] = []
    for part in page_spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start = int(start.strip())
            end = int(end.strip())
            for p in range(start, end + 1):
                if 1 <= p <= total_pages:
                    indices.append(p - 1)
        else:
            p = int(part)
            if 1 <= p <= total_pages:
                indices.append(p - 1)
    return sorted(set(indices))
