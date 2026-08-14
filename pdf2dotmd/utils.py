"""Utility helpers for markdown generation."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional


def escape_markdown(text: str) -> str:
    """Escape markdown control characters in plain text content."""
    if not text:
        return ""

    escaped = text.replace("\\", "\\\\")
    for ch in ["`", "*", "_", "{", "}", "[", "]", "<", ">", "|"]:
        escaped = escaped.replace(ch, f"\\{ch}")
    # A literal '#' at the start of the text would turn the emitted line into a
    # false ATX heading (e.g. a table's '#' number-column captured as text).
    # Escape the leading run so it renders as literal text.
    escaped = re.sub(r"^(#{1,6})(?=\s|$)", r"\\\1", escaped)
    return escaped


def clean_markdown_content(lines: Iterable[str]) -> str:
    """Join markdown lines and collapse repeated blank lines."""
    text = "\n".join(lines).rstrip() + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


# Bare URLs/emails to autolink: http(s)://, scheme-less www., and emails.
# Not preceded by '<' or ']' (already wrapped / link text) nor by '](' (a
# markdown link target) — but a bare URL inside plain "(..." IS autolinked.
_BARE_LINK_RE = re.compile(
    r"(?<![<\]])(?<!\]\()(?:"
    r"https?://[^\s<>)\]\"]+"
    r"|www\.[A-Za-z0-9][^\s<>)\]\"]+"
    r"|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r")"
)
_LIST_ITEM_RE = re.compile(r"^\s*([-*+]|\d+\.)\s+\S")


def _classify_block(line: str) -> str:
    """Coarse block classification for blank-line insertion."""
    stripped = line.strip()
    if not stripped:
        return "blank"
    left = line.lstrip()
    if left.startswith("#"):
        return "heading"
    if left.startswith("```") or left.startswith("~~~"):
        return "fence"
    if left.startswith("|"):
        return "table"
    if _LIST_ITEM_RE.match(line):
        return "list-ol" if re.match(r"^\s*\d+\.", line) else "list-ul"
    return "text"


# A table delimiter cell: "---", ":--", "--:", ":--:".
_DELIM_CELL_RE = re.compile(r"^\s*:?-+:?\s*$")


def _split_table_row(line: str) -> List[str]:
    """Split a markdown table row into cells, honoring escaped pipes (``\\|``)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    # Strip a single trailing pipe unless it is an escaped pipe at end of cell.
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _format_table_row(cells: List[str]) -> str:
    """Render a markdown table row in pipe style.

    Each non-empty cell is wrapped `` content ``; an *empty* cell is rendered
    as a single space (``| |``) rather than two (``|  |``). Two consecutive
    spaces inside a cell read as "extra space for style compact" and trip
    MD060 (table-column-style), so the empty-cell special case is what keeps
    optional-column tables lint-clean. Non-empty rows are byte-identical to
    ``"| " + " | ".join(cells) + " |"``.
    """
    regions = [(" " + c.strip() + " ") if c.strip() else " " for c in cells]
    return "|" + "|".join(regions) + "|"


def _rectangularize_tables(lines: List[str]) -> List[str]:
    """Normalize table blocks so every row has the same column count (MD056).

    Heuristic table extraction (pdfplumber) and some PDFs themselves emit
    rows with differing cell counts, which markdownlint rejects. For each run
    of consecutive table rows, pad every row to the widest row's column count
    — empty cells for data rows, ``---`` for the delimiter row — so the table
    is rectangular. No data is dropped; this is a no-op for already-rectangular
    tables (e.g. docling output).
    """
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].lstrip().startswith("|"):
            out.append(lines[i])
            i += 1
            continue
        # Gather the contiguous run of table rows.
        run: List[str] = []
        while i < n and lines[i].lstrip().startswith("|"):
            run.append(lines[i])
            i += 1
        rows = [_split_table_row(r) for r in run]
        width = max((len(r) for r in rows), default=0)
        if width == 0:
            out.extend(run)
            continue
        for r in rows:
            is_delim = bool(r) and all(_DELIM_CELL_RE.match(c) for c in r) and any(c.strip() for c in r)
            pad = width - len(r)
            if pad > 0:
                r = r + (["---"] * pad if is_delim else [""] * pad)
            out.append(_format_table_row(r))
    return out


def sanitize_markdown(text: str) -> str:
    """Normalize generated markdown so it passes markdownlint structural rules.

    Fixes genuine correctness/readability rules — not style:

    * MD009 trailing whitespace      — strip per line (outside code blocks)
    * MD022 blanks around headings   — separate from neighbouring blocks
    * MD031 blanks around fences     — separate from neighbouring blocks
    * MD032 blanks around lists      — separate from neighbouring blocks
    * MD034 bare URLs                — wrap in ``<…>``
    * MD056 table column count       — rectangularize table rows
    * MD058 blanks around tables     — separate from neighbouring blocks
    * MD060 table column style       — empty cells render as one space, not two

    Style-only rules (line length MD013, ordered-list prefix style MD029,
    first-line-h1 MD041, emphasis-as-heading MD036) are intentionally *not*
    enforced here: they conflict with faithfully *generated* content and are
    disabled via the project ``.markdownlint.json`` instead.
    """
    lines = text.split("\n")
    # MD056: make every table rectangular before any other normalization.
    lines = _rectangularize_tables(lines)
    kinds = [_classify_block(line) for line in lines]

    # Track which lines fall inside a fenced code block (preserve their bytes).
    in_code = []
    open_fence = False
    for kind in kinds:
        if kind == "fence":
            open_fence = not open_fence
            in_code.append(False)
        else:
            in_code.append(open_fence)

    # MD009 + MD034: strip trailing whitespace and wrap bare URLs, but only
    # outside fenced code blocks (leave code verbatim).
    for i, line in enumerate(lines):
        if in_code[i] or kinds[i] == "fence":
            continue
        if line != line.rstrip():
            line = line.rstrip()
        line = _BARE_LINK_RE.sub(
            lambda m: "<" + m.group(0).rstrip(".,;:!?") + ">", line
        )
        lines[i] = line
        kinds[i] = _classify_block(line)

    # MD022/MD031/MD032/MD058: insert a blank line at every block boundary
    # (heading, list, table, fence). Code-block interiors are copied verbatim.
    out: list[str] = []
    prev_kind: Optional[str] = None
    in_fence = False
    for line, kind in zip(lines, kinds):
        if kind == "fence":
            if not in_fence and out and out[-1] != "":
                out.append("")  # blank before an opening fence
            out.append(line)
            in_fence = not in_fence
            prev_kind = "fence"
            continue
        if in_fence:
            out.append(line)  # preserve code block contents untouched
            continue
        if kind == "blank":
            out.append("")
            prev_kind = None
            continue
        if out and out[-1] != "" and _needs_blank_between(prev_kind, kind):
            out.append("")
        out.append(line)
        prev_kind = kind

    return clean_markdown_content(out)


def _needs_blank_between(prev: Optional[str], kind: str) -> bool:
    """Whether two adjacent non-blank block runs require a separating blank."""
    if prev is None:
        return False
    # A run of paragraph lines or table rows stays contiguous.
    if prev == kind and kind in ("text", "table"):
        return False
    # List runs stay contiguous only within the same marker class. docling
    # sometimes interleaves '-' and '1.' markers on adjacent lines; markdownlint
    # treats each unordered<->ordered switch as a new list needing a blank line.
    if prev in ("list-ul", "list-ol") and kind in ("list-ul", "list-ol"):
        return prev != kind
    return True


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
