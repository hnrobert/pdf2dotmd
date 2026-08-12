"""Conversion backends for pdf2dotmd.

A backend turns one input file into Markdown (plus optional image side
effects). The default :class:`~pdf2dotmd.backends.pdfplumber_backend.PdfPlumberBackend`
ships with the core install; heavier backends (e.g. docling) are provided by
optional extras and discovered via entry points — see :mod:`pdf2dotmd.plugins`.
"""

from .base import (
    Backend,
    BackendNotFoundError,
    BackendNotInstalledError,
    BackendResult,
    ConversionContext,
)

__all__ = [
    "Backend",
    "BackendNotFoundError",
    "BackendNotInstalledError",
    "BackendResult",
    "ConversionContext",
]
