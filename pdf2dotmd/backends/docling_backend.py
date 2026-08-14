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
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple, cast

from ..utils import sanitize_markdown
from .base import BackendNotInstalledError, BackendResult, ConversionContext

if TYPE_CHECKING:
    # PIL ships ``py.typed`` stubs; only needed for the image-return annotation,
    # so it is imported solely under TYPE_CHECKING (no runtime cost / dependency).
    from PIL.Image import Image

logger = logging.getLogger(__name__)

# docling's default export emits this literal placeholder for each picture.
_IMAGE_PLACEHOLDER = "<!-- image -->"


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
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        generate_images = not ctx.ignore_images and bool(ctx.assets_dir)
        pipeline_options = PdfPipelineOptions(
            generate_picture_images=generate_images,
        )
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )

        ranges = _page_range_to_docling(ctx.pages)
        logger.info(
            "docling backend: initializing (first run downloads models, "
            "may take a while)..."
        )

        # docling's convert() accepts a single inclusive (start, end) page
        # range, so a multi-range spec ("1-5,8") needs one pass per range.
        markdown_parts: List[str] = []
        pictures: list = []
        try:
            for start, end in ranges:
                result = converter.convert(
                    ctx.input_path, page_range=(start, end)
                )
                markdown_parts.append(result.document.export_to_markdown())
                pictures.extend(getattr(result.document, "pictures", []) or [])
            if not ranges:
                result = converter.convert(ctx.input_path)
                markdown_parts.append(result.document.export_to_markdown())
                pictures.extend(getattr(result.document, "pictures", []) or [])
        except Exception as exc:  # pylint: disable=broad-except
            raise RuntimeError(
                f"docling conversion failed: {exc}. "
                "If this is a network/model error, models download on first use."
            ) from exc

        markdown = "\n\n".join(part.rstrip() for part in markdown_parts)

        saved_paths: List[str] = []
        if generate_images:
            saved_paths = _save_pictures(pictures, ctx.assets_dir)

        if saved_paths:
            markdown = _rewrite_placeholders(markdown, saved_paths)
        elif ctx.ignore_images or not generate_images:
            # Drop the picture placeholders: either the user asked for
            # text-only output, or we have no saved images to point at.
            markdown = markdown.replace(_IMAGE_PLACEHOLDER, "")

        return BackendResult(markdown=sanitize_markdown(markdown))


def _page_range_to_docling(
    pages_spec: Optional[str],
) -> List[Tuple[int, int]]:
    """Convert a '1-5,8,10-12' spec into docling's inclusive (start, end) ranges.

    Returns ``[]`` when no page restriction is requested (convert the whole
    document). docling's ``convert()`` takes a *single* inclusive range per
    call, so each comma-separated segment becomes its own ``(start, end)``
    tuple.
    """
    if not pages_spec:
        return []
    ranges: List[Tuple[int, int]] = []
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
    return ranges


def _picture_page_number(pic) -> int:
    """Best-effort 1-based page number for a docling picture item (0 if unknown)."""
    prov = getattr(pic, "prov", None) or []
    if not prov and hasattr(pic, "provenance"):
        prov = getattr(pic, "provenance", []) or []
    for item in prov:
        for attr in ("page_no", "page"):
            value = getattr(item, attr, None)
            if value:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
    return 0


def _extract_pil_image(pic, document) -> Optional[Image]:
    """Extract a PIL image from a docling picture item across API versions."""
    # Newer docling: pic.get_image(document) (may be a context manager).
    getter = getattr(pic, "get_image", None)
    if callable(getter):
        try:
            image = getter(document)
            if image is not None:
                return cast(Optional[Image], image)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("pic.get_image() failed: %s", exc)
    # Older/alternate: pic.image.pil_image. ``getattr`` with a ``None`` default
    # infers as ``object``; at runtime this is a PIL Image, so cast accordingly.
    image_attr = getattr(pic, "image", None)
    return cast(Optional[Image], getattr(image_attr, "pil_image", None))


def _save_pictures(pictures: list, assets_dir: str) -> List[str]:
    """Save each picture into ``assets_dir`` using ImageExtractor-style naming.

    Returns the list of relative ``assets/<filename>`` paths in order. This is
    best-effort: docling's picture API has varied across versions, so any
    failure degrades gracefully (that picture is skipped).
    """
    if not pictures:
        return []
    os.makedirs(assets_dir, exist_ok=True)
    saved: List[str] = []
    for idx, pic in enumerate(pictures, start=1):
        image = _extract_pil_image(pic, None)
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


def _rewrite_placeholders(markdown: str, saved_paths: List[str]) -> str:
    """Replace ``<!-- image -->`` placeholders with ``assets/`` image refs.

    Replaces in order of appearance. If there are more placeholders than saved
    images, the surplus placeholders are dropped; if fewer, the surplus saved
    paths are ignored.
    """
    if not saved_paths:
        return markdown.replace(_IMAGE_PLACEHOLDER, "")

    refs = list(saved_paths)
    out: List[str] = []
    last_end = 0
    start = 0
    while True:
        idx = markdown.find(_IMAGE_PLACEHOLDER, start)
        if idx == -1:
            break
        out.append(markdown[last_end:idx])
        out.append(f"![image]({refs.pop(0)})" if refs else "")
        last_end = idx + len(_IMAGE_PLACEHOLDER)
        start = last_end
        if not refs:
            break
    out.append(markdown[last_end:])
    # Drop any placeholders we didn't get to replace.
    return "".join(out).replace(_IMAGE_PLACEHOLDER, "")
