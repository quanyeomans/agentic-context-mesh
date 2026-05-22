"""kairix extractor plugin layer.

Per-format-family plugins live under ``kairix/extractors/<name>/`` and
register via the ``kairix.extractors`` Python entry-point group in
their distribution's ``pyproject.toml``. Core code never imports a
concrete extractor — only the ``Extractor`` Protocol defined here.

See ``docs/architecture/connector-ingestion-architecture.md`` § 2 + § 8
for the architectural decision record and ``kairix-pro-platform`` ADR-019
for the plugin-boundary discipline (PEP 561 marker, frozen dataclasses
at the boundary, mypy-strict per plugin). The F34-F37 / F40-F43 fitness
functions enforce this split.

Public surface (Wave 1):

- ``Extractor`` — Protocol every plugin satisfies (``name``, ``version``,
  ``can_extract``, ``extract``, ``quality_ok``).
- ``ExtractedDocument`` — frozen dataclass returned by ``Extractor.extract``.
- ``Page`` / ``Image`` / ``DocMetadata`` — frozen-dataclass value objects
  composing ``ExtractedDocument``.
- ``MimeType`` — string alias for an IANA mime type identifier.

Wave 2 lands the ``markitdown`` + ``passthrough`` plugins. Wave 3 lands
the ``pdf_fallback`` + ``ocr`` plugins. No concrete extractors live in
this tree yet.
"""

from __future__ import annotations

from kairix.extractors._base import (
    DocMetadata,
    ExtractedDocument,
    Extractor,
    Image,
    MimeType,
    Page,
)

__all__ = [
    "DocMetadata",
    "ExtractedDocument",
    "Extractor",
    "Image",
    "MimeType",
    "Page",
]
