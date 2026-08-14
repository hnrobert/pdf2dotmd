"""Layout analysis for reconstructing reading order from PDF spatial data."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Optional

from .text_block import TextBlock

logger = logging.getLogger(__name__)

# Minimum gap (as fraction of page width) to detect a column boundary
COLUMN_GAP_MIN_FRACTION = 0.15

# Y-tolerance for grouping characters into lines (points)
DEFAULT_Y_TOLERANCE = 3.0

# Maximum vertical gap (as multiple of line height) to group lines into a block
BLOCK_MAX_GAP_LINES = 1.5

# Minimum horizontal overlap ratio for lines to be in the same block
BLOCK_MIN_OVERLAP = 0.5


class _TextLine:
    """Internal intermediate: a single line of characters on a page."""

    __slots__ = ("chars", "text", "x0", "y0", "x1", "y1", "font_name", "font_size")

    def __init__(self):
        self.chars: list[dict] = []
        self.text: str = ""
        self.x0: float = float("inf")
        self.y0: float = float("inf")
        self.x1: float = 0.0
        self.y1: float = 0.0
        self.font_name: str = ""
        self.font_size: float = 0.0

    def add_char(self, char: dict):
        self.chars.append(char)
        self.x0 = min(self.x0, char["x0"])
        self.y0 = min(self.y0, char["y0"])
        self.x1 = max(self.x1, char["x1"])
        self.y1 = max(self.y1, char["y1"])

    def finalize(self):
        if not self.chars:
            return
        self.chars.sort(key=lambda c: c["x0"])
        self.text = "".join(c["text"] for c in self.chars)
        # Dominant font
        font_counter = Counter(c.get("fontname", "") for c in self.chars)
        self.font_name = font_counter.most_common(1)[0][0]
        size_counter = Counter(c.get("size", 0) for c in self.chars)
        self.font_size = size_counter.most_common(1)[0][0]

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class LayoutAnalyzer:
    """Analyze PDF page layout to reconstruct reading order."""

    def __init__(self):
        self._header_texts: list[str] = []
        self._footer_texts: list[str] = []

    def analyze(self, page, page_number: int) -> list[TextBlock]:
        """Analyze a pdfplumber page and return TextBlocks in reading order."""
        chars = page.chars
        if not chars:
            logger.debug("Page %d has no text characters", page_number)
            return []

        page_width = page.width
        page_height = page.height

        # Step 1: Group characters into text lines
        lines = self._group_chars_to_lines(chars)

        # Step 2: Group lines into text blocks (paragraphs)
        blocks = self._group_lines_to_blocks(lines, page_number)

        # Step 3: Detect columns
        columns = self._detect_columns(blocks, page_width)

        # Step 4: Sort blocks into reading order
        ordered = self._apply_reading_order(blocks, columns, page_width)

        # Step 5: Detect headers/footers
        self._detect_headers_footers(ordered, page_height)

        # Step 6: Merge hyphenated line breaks
        self._merge_hyphenation(ordered)

        return ordered

    def analyze_multi_page(self, pages_with_numbers: list[tuple]) -> list[TextBlock]:
        """Analyze multiple pages and detect repeated headers/footers.

        pages_with_numbers: list of (page, page_number) tuples
        """
        all_blocks: list[TextBlock] = []
        per_page_top: list[tuple[str, int]] = []
        per_page_bottom: list[tuple[str, int]] = []

        for page, page_number in pages_with_numbers:
            blocks = self.analyze(page, page_number)
            if blocks:
                # topmost block (highest y1 in PDF coords = visually top)
                top_block = max(blocks, key=lambda b: b.y1)
                per_page_top.append((top_block.text.strip(), page_number))
                # bottommost block
                bottom_block = min(blocks, key=lambda b: b.y0)
                per_page_bottom.append((bottom_block.text.strip(), page_number))
            all_blocks.extend(blocks)

        # Mark repeated headers/footers across pages
        self._mark_repeated_elements(all_blocks, per_page_top, per_page_bottom)

        return all_blocks

    def _group_chars_to_lines(self, chars: list[dict]) -> list[_TextLine]:
        """Group characters into text lines by y-coordinate clustering."""
        if not chars:
            return []

        # Some PDF generators emit every glyph twice at the exact same
        # coordinates (a faux-bold / export artifact). Drop exact duplicate
        # glyphs first, otherwise each character is doubled in the output.
        # Two glyphs with identical text and identical position are never
        # legitimate adjacent characters (those are separated by their
        # advance width), so this is safe.
        seen: set = set()
        deduped: list[dict] = []
        for c in chars:
            key = (c["text"], round(c["x0"], 2), round(c["y0"], 2))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(c)
        chars = deduped

        # Sort by y center descending (top of page first), then x. Use the same
        # bottom-origin y0/y1 system that _TextLine stores, so the center-y
        # comparison below is consistent (top/bottom are top-origin and must
        # not be mixed in here).
        sorted_chars = sorted(chars, key=lambda c: (-(c["y0"] + c["y1"]) / 2, c["x0"]))

        lines: list[_TextLine] = []
        current_line = _TextLine()

        for char in sorted_chars:
            char_cy = (char["y0"] + char["y1"]) / 2
            # Use adaptive tolerance based on font size
            tolerance = max(DEFAULT_Y_TOLERANCE, char.get("size", 12) * 0.4)

            if current_line.chars:
                line_cy = current_line.center_y
                if abs(char_cy - line_cy) <= tolerance:
                    current_line.add_char(char)
                else:
                    current_line.finalize()
                    if current_line.text.strip():
                        lines.append(current_line)
                    current_line = _TextLine()
                    current_line.add_char(char)
            else:
                current_line.add_char(char)

        if current_line.chars:
            current_line.finalize()
            if current_line.text.strip():
                lines.append(current_line)

        # Sort lines top-to-bottom
        lines.sort(key=lambda l: -l.center_y)
        return lines

    def _group_lines_to_blocks(self, lines: list[_TextLine], page_number: int) -> list[TextBlock]:
        """Group adjacent text lines into TextBlock paragraphs."""
        if not lines:
            return []

        blocks: list[TextBlock] = []
        current_lines: list[_TextLine] = [lines[0]]

        for i in range(1, len(lines)):
            prev = current_lines[-1]
            curr = lines[i]

            # Check horizontal overlap
            overlap = self._horizontal_overlap(prev, curr)

            # Check vertical gap
            avg_height = (prev.height + curr.height) / 2
            if avg_height <= 0:
                avg_height = 12
            gap = prev.y0 - curr.y1  # positive means curr is below prev
            if gap < 0:
                gap = 0

            if overlap >= BLOCK_MIN_OVERLAP and gap < BLOCK_MAX_GAP_LINES * avg_height:
                current_lines.append(curr)
            else:
                blocks.append(self._lines_to_block(current_lines, page_number))
                current_lines = [curr]

        if current_lines:
            blocks.append(self._lines_to_block(current_lines, page_number))

        return blocks

    def _horizontal_overlap(self, a: _TextLine, b: _TextLine) -> float:
        """Calculate horizontal overlap ratio between two lines."""
        overlap = max(0.0, min(a.x1, b.x1) - max(a.x0, b.x0))
        min_width = min(a.width, b.width)
        if min_width <= 0:
            return 0.0
        return overlap / min_width

    def _lines_to_block(self, lines: list[_TextLine], page_number: int) -> TextBlock:
        """Merge multiple lines into a single TextBlock."""
        if not lines:
            return TextBlock(page_number=page_number)

        text_parts: list[str] = []
        for i, line in enumerate(lines):
            stripped = line.text.strip()
            if i > 0 and stripped:
                text_parts.append(" ")
            text_parts.append(stripped)

        text = "".join(text_parts).strip()

        # Dominant font info
        font_counter = Counter(l.font_name for l in lines)
        font_name = font_counter.most_common(1)[0][0]
        size_counter = Counter(l.font_size for l in lines)
        font_size = size_counter.most_common(1)[0][0]

        bold = "bold" in font_name.lower()

        return TextBlock(
            text=text,
            x0=min(l.x0 for l in lines),
            y0=min(l.y0 for l in lines),
            x1=max(l.x1 for l in lines),
            y1=max(l.y1 for l in lines),
            font_name=font_name,
            font_size=font_size,
            bold=bold,
            page_number=page_number,
        )

    def _detect_columns(self, blocks: list[TextBlock], page_width: float) -> list[tuple[float, float]]:
        """Detect column boundaries. Returns list of (x0, x1) column ranges."""
        if not blocks or page_width <= 0:
            return [(0, page_width)]

        # Collect block left edges
        left_edges = sorted(set(round(b.x0) for b in blocks))

        if len(left_edges) <= 1:
            return [(0, page_width)]

        # Find the largest horizontal gap between block groups
        # Divide page into vertical strips and count chars density
        strip_width = page_width / 40
        strip_counts: dict[int, int] = {}
        for b in blocks:
            center_strip = int(b.center_x / strip_width)
            strip_counts[center_strip] = strip_counts.get(center_strip, 0) + len(b.text)

        # Find gap ranges with no text
        all_strips = range(40)
        gap_ranges: list[tuple[int, int]] = []
        gap_start = None

        for s in all_strips:
            if strip_counts.get(s, 0) == 0:
                if gap_start is None:
                    gap_start = s
            else:
                if gap_start is not None:
                    gap_ranges.append((gap_start, s - 1))
                    gap_start = None
        if gap_start is not None:
            gap_ranges.append((gap_start, 39))

        # Filter gaps that are wide enough to be column separators
        min_gap_strips = int(COLUMN_GAP_MIN_FRACTION * 40)
        significant_gaps = [
            (s * strip_width, (e + 1) * strip_width)
            for s, e in gap_ranges
            if (e - s + 1) >= min_gap_strips
        ]

        if not significant_gaps:
            return [(0, page_width)]

        # Build column ranges from gaps
        columns: list[tuple[float, float]] = []
        prev_end = 0.0
        for gap_start, gap_end in significant_gaps:
            if gap_start > prev_end:
                columns.append((prev_end, gap_start))
            prev_end = gap_end
        if prev_end < page_width:
            columns.append((prev_end, page_width))

        if len(columns) <= 1:
            return [(0, page_width)]

        return columns

    def _apply_reading_order(
        self,
        blocks: list[TextBlock],
        columns: list[tuple[float, float]],
        page_width: float,
    ) -> list[TextBlock]:
        """Sort blocks into logical reading order based on detected columns."""
        if len(columns) <= 1:
            # Single column: sort top-to-bottom (descending y1 in PDF coords)
            return sorted(blocks, key=lambda b: -b.y1)

        # Multi-column layout
        column_blocks: list[list[TextBlock]] = [[] for _ in columns]
        spanning: list[TextBlock] = []

        for block in blocks:
            if block.is_spanning(page_width):
                spanning.append(block)
            else:
                # Assign to column by center_x
                best_col = 0
                best_dist = float("inf")
                for i, (cx0, cx1) in enumerate(columns):
                    col_center = (cx0 + cx1) / 2
                    dist = abs(block.center_x - col_center)
                    if dist < best_dist:
                        best_dist = dist
                        best_col = i
                column_blocks[best_col].append(block)

        # Sort each column top-to-bottom
        for col in column_blocks:
            col.sort(key=lambda b: -b.y1)

        # Sort spanning blocks top-to-bottom
        spanning.sort(key=lambda b: -b.y1)

        # Merge: interleaving spanning blocks with column content
        result: list[TextBlock] = []
        col_indices = [0] * len(columns)
        span_idx = 0

        for block in spanning:
            # Flush column blocks that are above this spanning block
            for col_i, col in enumerate(column_blocks):
                while col_indices[col_i] < len(col) and col[col_indices[col_i]].y1 > block.y1:
                    result.append(col[col_indices[col_i]])
                    col_indices[col_i] += 1
            result.append(block)

        # Flush remaining column blocks (left to right, top to bottom within)
        while any(col_indices[i] < len(column_blocks[i]) for i in range(len(columns))):
            # Find the topmost remaining block across all columns
            top_block = None
            top_col = -1
            for col_i, col in enumerate(column_blocks):
                if col_indices[col_i] < len(col):
                    b = col[col_indices[col_i]]
                    if top_block is None or b.y1 > top_block.y1:
                        top_block = b
                        top_col = col_i
            if top_block is None:
                break
            result.append(top_block)
            col_indices[top_col] += 1

        return result

    def _detect_headers_footers(self, blocks: list[TextBlock], page_height: float):
        """Detect header/footer blocks based on position on the page."""
        if not blocks:
            return

        for block in blocks:
            # Top 10% of page → potential header
            if block.y1 > page_height * 0.92:
                block.is_header = True
            # Bottom 8% of page → potential footer
            if block.y0 < page_height * 0.08:
                footer_text = block.text.strip()
                if re.match(r"^[-—\s]*\d+[-—\s]*$", footer_text):
                    block.is_footer = True
                elif re.match(r"^\d+\s*/\s*\d+$", footer_text):
                    block.is_footer = True

    def _mark_repeated_elements(
        self,
        all_blocks: list[TextBlock],
        per_page_top: list[tuple[str, int]],
        per_page_bottom: list[tuple[str, int]],
    ):
        """Mark blocks as headers/footers if they repeat across pages."""
        if len(per_page_top) < 3:
            return

        # Check for repeated top texts
        top_texts = [t for t, _ in per_page_top]
        top_counter = Counter(top_texts)
        for text, count in top_counter.items():
            if count >= 3:
                for block in all_blocks:
                    if block.text.strip() == text:
                        block.is_header = True

        # Check for repeated bottom texts
        bottom_texts = [t for t, _ in per_page_bottom]
        bottom_counter = Counter(bottom_texts)
        for text, count in bottom_counter.items():
            if count >= 3:
                for block in all_blocks:
                    if block.text.strip() == text:
                        block.is_footer = True

    def _merge_hyphenation(self, blocks: list[TextBlock]):
        """Merge hyphenated words split across consecutive blocks."""
        i = 0
        while i < len(blocks) - 1:
            current = blocks[i]
            next_block = blocks[i + 1]
            if (
                current.text.endswith("-")
                and next_block.text
                and next_block.text[0].islower()
                and not current.is_header
                and not current.is_footer
                and not next_block.is_header
                and not next_block.is_footer
            ):
                # Remove hyphen and join
                current.text = current.text[:-1] + next_block.text
                # Expand bounding box
                current.x1 = max(current.x1, next_block.x1)
                current.y0 = min(current.y0, next_block.y0)
                blocks.pop(i + 1)
                # Don't increment i - check again in case of multi-line hyphenation
            else:
                i += 1

    @staticmethod
    def infer_heading_levels(blocks: list[TextBlock]) -> dict[int, int]:
        """Infer heading levels (1-6) from font sizes. Returns {block_index: level}."""
        if not blocks:
            return {}

        # Collect font sizes from non-header/footer blocks
        sizes = [b.font_size for b in blocks if not b.is_header and not b.is_footer]
        if not sizes:
            return {}

        # Body text = most common size
        size_counter = Counter(sizes)
        body_size = size_counter.most_common(1)[0][0]

        # Find unique sizes larger than body
        larger_sizes = sorted(set(s for s in sizes if s > body_size * 1.1), reverse=True)

        heading_map: dict[int, int] = {}
        for idx, block in enumerate(blocks):
            if block.is_header or block.is_footer:
                continue
            if block.font_size > body_size * 1.1:
                try:
                    level = larger_sizes.index(block.font_size) + 1
                except ValueError:
                    level = 6
                level = min(level, 6)
                heading_map[idx] = level

        return heading_map

    @staticmethod
    def compute_heading_size_map(all_blocks: list[TextBlock]) -> dict:
        """Build a document-wide ``{font_size: heading_level}`` map.

        Unlike :meth:`infer_heading_levels` (which ranks sizes *per page* and
        therefore assigns different levels to the same font size on different
        pages), this ranks the heading sizes once across the whole document so
        a given font size always maps to the same level. The single largest
        heading size is level 1; combined with the single-H1 enforcement in
        :class:`PageProcessor` this yields exactly one ``#`` title per document.

        Returns an empty map when there are no heading-sized blocks.
        """
        # Round to 1 decimal: PDF font sizes are "nice" numbers (10, 11, 14,
        # 18, 26, ...) but pdfplumber reports them with float noise
        # (17.9999 / 18.0001), which would otherwise split one real size into
        # several spurious heading ranks.
        sizes = [
            round(b.font_size, 1)
            for b in all_blocks
            if not b.is_header and not b.is_footer
        ]
        if not sizes:
            return {}
        body_size = Counter(sizes).most_common(1)[0][0]
        larger = sorted({s for s in sizes if s > body_size * 1.1}, reverse=True)
        return {s: i + 1 for i, s in enumerate(larger)}
