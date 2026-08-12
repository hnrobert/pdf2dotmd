"""Plugin registry and backend discovery.

Two concepts are kept apart:

* :data:`KNOWN_BACKENDS` — a static catalogue of installable first-party
  backends (name → install spec / description / min Python). This lets
  ``plugin install/list/info`` describe backends even before their optional
  dependencies are importable.
* Entry-point discovery — :func:`entry_points` under the
  ``pdf2dotmd.backends`` group — reports what is actually importable right now,
  including third-party packages (e.g. a future ``pdf2dotmd-foo``).

First-party backends are also held in :data:`_BUILTIN_BACKENDS` so resolution
does not depend on the package being *installed* (matters when running from a
source checkout without entry points registered).
"""

from __future__ import annotations

import importlib.metadata
import logging
from dataclasses import dataclass
from typing import Optional, Type

try:
    import pdfplumber  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    pdfplumber = None  # type: ignore[assignment]

from .backends.base import Backend, BackendNotFoundError, BackendNotInstalledError
from .backends.docling_backend import DoclingBackend
from .backends.pdfplumber_backend import PdfPlumberBackend

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pdf2dotmd.backends"

# Friendly message shown everywhere a missing backend is reported.
INSTALL_HINT_TEMPLATE = (
    "Backend '{name}' is not installed.\n"
    "Run: pdf2dotmd plugin install {name}"
)

# Catalogue of installable first-party backends.
KNOWN_BACKENDS: dict[str, dict] = {
    "docling": {
        "install_spec": "pdf2dotmd[docling]",
        "uninstall_targets": ["docling"],
        "description": (
            "ML backend: OCR + complex tables + layout "
            "(TableFormer/DocLayNet). Needs Python >=3.10."
        ),
        "min_python": (3, 10),
    },
}

# Direct references to built-in backends (always resolvable without entry points).
_BUILTIN_BACKENDS: dict[str, Type] = {
    "pdfplumber": PdfPlumberBackend,
    "docling": DoclingBackend,
}


@dataclass
class BackendInfo:
    """Discovered backend metadata for ``plugin list``."""

    name: str
    installed: bool
    description: str
    install_spec: Optional[str]
    source: str  # "builtin" | "known" | "entrypoint"


def _entry_points_for(group: str):
    """Return entry points for ``group`` across Python 3.8–3.13."""
    eps = importlib.metadata.entry_points()
    if hasattr(eps, "select"):  # Python 3.10+
        return eps.select(group=group)
    return eps.get(group, [])  # Python 3.8/3.9 (dict-style)


def _probe_available(cls: Type) -> bool:
    """Instantiate ``cls`` and probe ``is_available()``, tolerating failures."""
    try:
        return bool(cls().is_available())
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("Backend %s.is_available() raised: %s", cls, exc)
        return False


def discover_backends() -> dict[str, BackendInfo]:
    """Return all known + discovered backends with their installed status."""
    found: dict[str, BackendInfo] = {}

    # 1. Built-in backends (always present in the package).
    for name, cls in _BUILTIN_BACKENDS.items():
        known = KNOWN_BACKENDS.get(name, {})
        found[name] = BackendInfo(
            name=name,
            installed=_probe_available(cls),
            description=known.get("description", ""),
            install_spec=known.get("install_spec"),
            source="builtin",
        )

    # 2. Catalogue entries that are not built-in classes (still installable).
    for name, meta in KNOWN_BACKENDS.items():
        if name in found:
            continue
        found[name] = BackendInfo(
            name=name,
            installed=False,
            description=meta.get("description", ""),
            install_spec=meta.get("install_spec"),
            source="known",
        )

    # 3. Third-party backends registered via entry points.
    for ep in _entry_points_for(ENTRY_POINT_GROUP):
        if ep.name in found:
            continue
        installed = False
        try:
            installed = _probe_available(ep.load())
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("Failed to load entry point %s: %s", ep.name, exc)
        found[ep.name] = BackendInfo(
            name=ep.name,
            installed=installed,
            description="Third-party backend",
            install_spec=None,
            source="entrypoint",
        )

    return found


def _backend_class(name: str) -> Optional[Type]:
    """Resolve a backend name to its class (built-in first, then entry points)."""
    if name in _BUILTIN_BACKENDS:
        return _BUILTIN_BACKENDS[name]

    for ep in _entry_points_for(ENTRY_POINT_GROUP):
        if ep.name == name:
            try:
                return ep.load()
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("Failed to load entry point %s: %s", name, exc)
                return None
    return None


def resolve_backend(name: str, input_path: Optional[str] = None) -> Backend:
    """Return an available backend instance for ``name``.

    ``name`` may be ``"auto"`` (pick based on the input), a built-in name
    (``"pdfplumber"``/``"docling"``), or a third-party entry-point name.
    Raises :class:`BackendNotInstalledError` if the backend is known but its
    dependencies are missing, or :class:`BackendNotFoundError` if unknown.
    """
    if name == "auto":
        return _select_auto(input_path)

    cls = _backend_class(name)
    if cls is None:
        raise BackendNotFoundError(name)

    instance = cls()
    if not _probe_available(type(instance)):
        raise BackendNotInstalledError(name)
    return instance


def pdf_has_text_layer(input_path: str, page_indices: Optional[list[int]] = None) -> bool:
    """Return True if the PDF has extractable text on any selected page."""
    if pdfplumber is None:
        return False
    try:
        with pdfplumber.open(input_path) as pdf:
            total = len(pdf.pages)
            indices = page_indices if page_indices is not None else range(total)
            for idx in indices:
                if 0 <= idx < total and pdf.pages[idx].chars:
                    return True
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("Failed to probe text layer of %s: %s", input_path, exc)
        return False
    return False


def _select_auto(input_path: Optional[str]) -> Backend:
    """Choose a backend automatically based on the input.

    Born-digital PDFs use the fast pdfplumber backend; scanned (no-text-layer)
    PDFs upgrade to docling when it is installed, otherwise fall back to
    pdfplumber with an install tip.
    """
    scanned = bool(input_path) and not pdf_has_text_layer(input_path)

    if scanned:
        if DoclingBackend.is_available():
            logger.info("No text layer detected; using 'docling' backend (OCR).")
            return DoclingBackend()
        # The pdfplumber backend emits its own no-text warning; add the tip.
        logger.warning(
            "Tip: install the OCR backend with: pdf2dotmd plugin install docling"
        )

    return PdfPlumberBackend()


__all__ = [
    "INSTALL_HINT_TEMPLATE",
    "KNOWN_BACKENDS",
    "BackendInfo",
    "BackendNotFoundError",
    "BackendNotInstalledError",
    "discover_backends",
    "pdf_has_text_layer",
    "resolve_backend",
]
