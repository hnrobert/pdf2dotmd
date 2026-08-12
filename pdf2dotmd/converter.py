"""Core converter module for PDF to Markdown conversion.

The converter is an orchestrator: it validates input, resolves the output
layout, picks a conversion :class:`~pdf2dotmd.backends.base.Backend` (default
``pdfplumber``; ``docling`` when the extra is installed), and writes the result.
The heavy lifting lives in the backends — see :mod:`pdf2dotmd.plugins`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from .backends.base import ConversionContext
from .plugins import (
    INSTALL_HINT_TEMPLATE,
    KNOWN_BACKENDS,
    BackendNotFoundError,
    BackendNotInstalledError,
    resolve_backend,
)

logger = logging.getLogger(__name__)


class PdfToMarkdownConverter:
    """PDF to Markdown converter."""

    def __init__(self):
        self.output_folder: str = ""
        self.assets_dir: str = ""

    def convert_file(
        self,
        input_path: str,
        output_path: Optional[str] = None,
        ignore_images: bool = False,
        pages: Optional[str] = None,
        *,
        backend: str = "auto",
    ) -> str:
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Input file does not exist: {input_path}")

        if not input_path.lower().endswith(".pdf"):
            raise ValueError(f"Only .pdf is supported: {input_path}")

        self._setup_output_structure(input_path, output_path, ignore_images)

        try:
            backend_obj = resolve_backend(backend, input_path=input_path)
        except BackendNotInstalledError:
            raise RuntimeError(INSTALL_HINT_TEMPLATE.format(name=backend))
        except BackendNotFoundError:
            available = ", ".join(["auto", "pdfplumber", *sorted(KNOWN_BACKENDS)])
            raise RuntimeError(f"Unknown backend '{backend}'. Available: {available}")

        ctx = ConversionContext(
            input_path=input_path,
            output_path=output_path,
            ignore_images=ignore_images,
            pages=pages,
            output_folder=self.output_folder,
            assets_dir=self.assets_dir,
        )

        result = backend_obj.convert(ctx)
        markdown_content = result.markdown

        final_output_path = self._get_final_output_path(input_path, output_path)
        self._write_output(markdown_content, final_output_path)
        self._cleanup_empty_assets_dir()

        logger.info(
            "Conversion completed (backend=%s), output file: %s",
            backend_obj.name,
            final_output_path,
        )
        return markdown_content

    def _setup_output_structure(
        self, input_path: str, output_path: Optional[str], ignore_images: bool
    ):
        input_stem = Path(input_path).stem

        if output_path:
            if os.path.isdir(output_path) or output_path.endswith("/"):
                self.output_folder = os.path.join(output_path, input_stem)
            else:
                self.output_folder = os.path.dirname(output_path)
                if not self.output_folder:
                    self.output_folder = input_stem
        else:
            self.output_folder = input_stem

        os.makedirs(self.output_folder, exist_ok=True)

        if ignore_images:
            self.assets_dir = ""
        else:
            self.assets_dir = os.path.join(self.output_folder, "assets")
            os.makedirs(self.assets_dir, exist_ok=True)

    def _get_final_output_path(self, input_path: str, output_path: Optional[str]) -> str:
        input_stem = Path(input_path).stem

        if output_path:
            if os.path.isdir(output_path) or output_path.endswith("/"):
                return os.path.join(self.output_folder, f"{input_stem}.md")
            return output_path

        return os.path.join(self.output_folder, f"{input_stem}.md")

    def _write_output(self, content: str, output_path: str):
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _cleanup_empty_assets_dir(self):
        if self.assets_dir and os.path.exists(self.assets_dir) and not os.listdir(self.assets_dir):
            os.rmdir(self.assets_dir)
