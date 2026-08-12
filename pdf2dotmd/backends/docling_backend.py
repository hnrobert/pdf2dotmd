"""docling-based conversion backend (optional — install via the ``docling`` extra).

Provides OCR for scanned PDFs plus ML-driven layout and table-structure
recognition (DocLayNet + TableFormer). All ``docling`` imports live inside
methods so the module imports successfully even when the extra is not
installed; :meth:`DoclingBackend.is_available` reports the true state.

Install with::

    pdf2dotmd plugin install docling

Note: docling v2 requires Python >=3.10. The core package remains installable
on older interpreters; only this backend is gated.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from pathlib import Path
from typing import Optional

from ..utils import clean_markdown_content
from .base import BackendNotInstalledError, BackendResult, ConversionContext

logger = logging.getLogger(__name__)

# Matches a Markdown image token: ![alt](url)
_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class DoclingBackend:
    """Convert PDFs to Markdown using IBM docling (OCR + layout + tables)."""

    name = "docling"

    @staticmethod
    @functools.lru_cache(maxsize=1)
    def is_available() -> bool:
        try:
            import docling  # noqa: F401  pylint: disable=import-outside-toplevel
        except ImportError:
            return False
        return True

    def convert(self, ctx: ConversionContext) -> BackendResult:
        if not self.is_available():
            raise BackendNotInstalledError("docling")

        # Heavy imports happen only when the backend is actually used.
        # pylint: disable=import-outside-toplevel
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import (
            PdfFormatOption,
            PdfPipelineOptions,
        )
        from docling.document_converter import DocumentConverter

        generate_images = not ctx.ignore_images and bool(ctx.assets_dir)
        pipeline_options = PdfPipelineOptions(
            generate_picture_images=generate_images,
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        page_range = _page_range_to_docling(ctx.pages)
        logger.info(
            "docling backend: initializing (first run downloads models, "
            "may take a while)..."
        )
        try:
            if page_range is not None:
                result = converter.convert(ctx.input_path, page_range=page_range)
            else:
                result = converter.convert(ctx.input_path)
        except TypeError:
            # Older docling versions do not accept page_range.
            logger.warning(
                "Installed docling does not support page_range; "
                "converting the whole document."
            )
            result = converter.convert(ctx.input_path)
        except Exception as exc:  # pylint: disable=broad-except
            raise RuntimeError(
                f"docling conversion failed: {exc}. "
                "If this is a network/model error, models download on first use."
            ) from exc

        document = result.document

        saved_paths: list[str] = []
        if generate_images:
            saved_paths = _save_pictures(document, ctx.assets_dir)

        markdown = document.export_to_markdown()

        if saved_paths:
            markdown = _rewrite_image_refs(markdown, saved_paths)
        elif ctx.ignore_images:
            markdown = _IMAGE_REF_RE.sub("", markdown)

        return BackendResult(markdown=clean_markdown_content(markdown.splitlines()))


def _page_range_to_docling(pages_spec: Optional[str]):
    """Convert a '1-5,8,10-12' spec into docling's 1-based inclusive ranges.

    Returns None when no page restriction is requested. docling expects a list
    of ``(start, end)`` inclusive 1-based tuples.
    """
    if not pages_spec:
        return None
    ranges = []
    for part in pages_spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            ranges.append((int(start.strip()), int(end.strip())))
        else:
            n = int(part)
            ranges.append((n, n))
    return ranges or None


def _picture_page_number(pic) -> int:
    """Best-effort page number for a docling picture item (0 if unknown)."""
    prov = getattr(pic, "prov", None) or []
    for item in prov:
        for attr in ("page_no", "page"):
            value = getattr(item, attr, None)
            if value:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return 0


def _extract_pil_image(pic, document):
    """Extract a PIL image from a docling picture item across API versions."""
    # Newer docling: pic.get_image(document) (may be a context manager).
    getter = getattr(pic, "get_image", None)
    if callable(getter):
        try:
            image = getter(document)
            if image is not None:
                return image
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("pic.get_image() failed: %s", exc)
    # Older/alternate: pic.image.pil_image
    image_attr = getattr(pic, "image", None)
    return getattr(image_attr, "pil_image", None)


def _save_pictures(document, assets_dir: str) -> list[str]:
    """Save each picture into ``assets_dir`` using ImageExtractor-style naming.

    Returns the list of relative ``assets/<filename>`` paths in order. This is
    best-effort: docling's picture API has varied across versions, so any
    failure degrades gracefully (that picture is skipped).
    """
    os.makedirs(assets_dir, exist_ok=True)
    pictures = getattr(document, "pictures", None) or []
    saved: list[str] = []
    for idx, pic in enumerate(pictures, start=1):
        image = _extract_pil_image(pic, document)
        if image is None:
            logger.debug("No extractable image for docling picture %d", idx)
            continue
        page = _picture_page_number(pic)
        filename = f"page{page:03d}_img{idx:02d}.png"
        output_path = Path(assets_dir) / filename
        try:
            image.save(output_path, format="PNG")
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Failed to save docling picture %d: %s", idx, exc)
            continue
        saved.append(f"assets/{filename}")
    return saved


def _rewrite_image_refs(markdown: str, saved_paths: list[str]) -> str:
    """Replace docling's image references with our saved ``assets/`` paths.

    Replaces image tokens in order of appearance. If docling emitted more
    references than we saved (or vice versa), the surplus is left untouched.
    """
    if not saved_paths:
        return markdown

    refs = list(saved_paths)
    out = []
    last_end = 0
    for match in _IMAGE_REF_RE.finditer(markdown):
        if not refs:
            break
        out.append(markdown[last_end:match.start()])
        out.append(f"![]({refs.pop(0)})")
        last_end = match.end()
    out.append(markdown[last_end:])
    return "".join(out)
