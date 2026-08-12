"""Backend abstraction: the contract every conversion backend implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class ConversionContext:
    """Inputs handed to a backend for a single conversion.

    The orchestrator (:class:`~pdf2dotmd.converter.PdfToMarkdownConverter`)
    resolves output paths and the assets directory before building this, so a
    backend only needs to read the input and produce Markdown.
    """

    input_path: str
    output_path: Optional[str]
    ignore_images: bool
    pages: Optional[str]  # "1-5,8,10-12" form, unchanged from the CLI
    output_folder: str
    assets_dir: str  # "" when ignore_images is True


@dataclass
class BackendResult:
    """Output of a backend conversion."""

    markdown: str


class Backend(Protocol):
    """A conversion backend.

    Backends are structural types — third-party plugins satisfy this Protocol
    without importing it. ``name`` identifies the backend (used by
    ``--backend`` and ``plugin list``); ``is_available`` is a cheap probe for
    whether the backend's optional dependencies are importable; ``convert``
    does the work and may write image files into ``ctx.assets_dir``.
    """

    name: str

    def is_available(self) -> bool:
        """Return True if the backend's dependencies are importable."""
        ...

    def convert(self, ctx: ConversionContext) -> BackendResult:
        """Convert ``ctx.input_path`` to Markdown."""
        ...


class BackendNotInstalledError(RuntimeError):
    """Raised when a requested backend's optional dependencies are missing."""


class BackendNotFoundError(KeyError):
    """Raised when an unknown backend name is requested."""
