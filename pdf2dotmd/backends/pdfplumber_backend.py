"""pdfplumber-based conversion backend (the default, lightweight backend).

This is the original pdf2dotmd pipeline — pure heuristic layout analysis with
no external models and no OCR. It is always available because ``pdfplumber``
is the sole core dependency.
"""

from __future__ import annotations

import logging

try:
    import pdfplumber  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore[assignment]

from ..image_extractor import ImageExtractor
from ..layout_analyzer import LayoutAnalyzer
from ..page_processor import PageProcessor
from ..table_processor import TableProcessor
from ..utils import clean_markdown_content, parse_page_range, sanitize_markdown
from .base import BackendResult, ConversionContext

logger = logging.getLogger(__name__)


class PdfPlumberBackend:
    """Convert PDFs to Markdown using pdfplumber + heuristic layout analysis."""

    name = "pdfplumber"

    @staticmethod
    def is_available() -> bool:
        return pdfplumber is not None

    def convert(self, ctx: ConversionContext) -> BackendResult:
        if pdfplumber is None:
            raise RuntimeError(
                "Missing required dependency 'pdfplumber'. "
                "Please run: pip install pdfplumber"
            )

        layout_analyzer = LayoutAnalyzer()
        table_processor = TableProcessor()
        image_extractor = (
            ImageExtractor(ctx.assets_dir)
            if not ctx.ignore_images and ctx.assets_dir
            else None
        )
        page_processor = PageProcessor(
            layout_analyzer=layout_analyzer,
            table_processor=table_processor,
            image_extractor=image_extractor,
            ignore_images=ctx.ignore_images,
        )

        output_lines: list[str] = []

        with pdfplumber.open(ctx.input_path) as pdf:
            total_pages = len(pdf.pages)

            if total_pages == 0:
                logger.warning("PDF has no pages: %s", ctx.input_path)
                return BackendResult(markdown="\n")

            if ctx.pages:
                page_indices = parse_page_range(ctx.pages, total_pages)
                if not page_indices:
                    raise ValueError(
                        f"No valid pages in range '{ctx.pages}' (total: {total_pages})"
                    )
            else:
                page_indices = list(range(total_pages))

            # Check whether the PDF has any extractable text on selected pages.
            has_text = False
            for idx in page_indices:
                if pdf.pages[idx].chars:
                    has_text = True
                    break
            if not has_text:
                logger.warning(
                    "PDF has no extractable text on the selected pages "
                    "(possibly scanned); output may be empty."
                )

            # Two-phase rendering: analyze every selected page once (caching
            # the blocks), build a document-wide heading-size map so a given
            # font size maps to the same heading level on every page (and only
            # one H1 is emitted), then render each page from its cached blocks.
            cached: list[tuple] = []  # (page, page_number, blocks)
            all_blocks: list = []
            for idx in page_indices:
                page = pdf.pages[idx]
                page_number = idx + 1
                blocks = layout_analyzer.analyze(page, page_number)
                cached.append((page, page_number, blocks))
                all_blocks.extend(blocks)

            page_processor.set_heading_context(
                LayoutAnalyzer.compute_heading_size_map(all_blocks)
            )

            for page, page_number, blocks in cached:
                logger.debug("Rendering page %d/%d", page_number, total_pages)
                page_lines = page_processor.process_page(
                    page, page_number, blocks=blocks
                )
                output_lines.extend(page_lines)

        markdown_content = sanitize_markdown(clean_markdown_content(output_lines))
        return BackendResult(markdown=markdown_content)
