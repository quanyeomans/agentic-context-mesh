"""Re-exports the Extractor Protocol and its value objects so plugin authors
import from ``kairix.extractors`` rather than reaching into core.

Production plugins ship under ``kairix/extractors/<name>/`` and expose a
``make_extractor`` factory function registered via the
``kairix.extractors`` entry-point group (see ``pyproject.toml`` and
``kairix-pro-platform`` ADR-019).

TODO(SC-1): once the canonical ``Extractor`` Protocol and its value
objects land in ``kairix.core.protocols``, delete the placeholder
definitions below and re-export them from there instead. Tracked by
the Connector-Framework Wave 1 SC-1 work item. The placeholders mirror
the shapes in ``docs/architecture/connector-ingestion-architecture.md``
§ 2 verbatim so swap-out is a pure import change.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

#: Alias for an IANA mime type identifier (e.g. ``"application/pdf"``).
#: Kept as a bare ``str`` alias at the boundary — frozen-dataclass
#: discipline is enforced on the surrounding value objects, not on this
#: primitive scalar.
MimeType = str


@dataclass(frozen=True)
class Page:
    """One per-page / per-slide / per-sheet extraction inside an
    ``ExtractedDocument``.

    Members:

    - ``index`` (``int``): zero-based page / slide / sheet index.
    - ``text`` (``str``): extracted text content for the page.
    - ``confidence`` (``float``): per-page extraction confidence in
      ``[0.0, 1.0]``; drives OCR fallback when low.
    """

    index: int
    text: str
    confidence: float


@dataclass(frozen=True)
class Image:
    """One image extracted from a document.

    Members:

    - ``page_index`` (``int``): zero-based page the image was extracted
      from.
    - ``mime`` (``MimeType``): image mime type (e.g. ``"image/png"``).
    - ``raw`` (``bytes``): raw image bytes.
    - ``caption`` (``str | None``): caption / alt-text, if classified.
    """

    page_index: int
    mime: MimeType
    raw: bytes
    caption: str | None = None


@dataclass(frozen=True)
class DocMetadata:
    """Format-agnostic document metadata extracted at parse time.

    Members:

    - ``title`` (``str | None``): document title if discoverable.
    - ``author`` (``str | None``): primary author if discoverable.
    - ``page_count`` (``int``): total pages / slides / sheets.
    - ``extra`` (``Mapping[str, str]``): per-format extras the
      ``Extractor`` chose to surface; treated as opaque by the rest
      of the pipeline.
    """

    title: str | None = None
    author: str | None = None
    page_count: int = 0
    extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractedDocument:
    """Output of ``Extractor.extract`` — markdown plus per-page,
    per-image, and per-document signals.

    Members:

    - ``markdown`` (``str``): primary markdown rendering of the
      document; downstream chunking consumes this.
    - ``pages`` (``tuple[Page, ...]``): per-page / per-slide /
      per-sheet extractions.
    - ``images`` (``tuple[Image, ...]``): extracted images.
    - ``metadata`` (``DocMetadata``): document-level metadata.
    - ``confidence`` (``float``): average extraction confidence;
      drives ``Extractor.quality_ok`` and the OCR-fallback decision.
    """

    markdown: str
    pages: tuple[Page, ...]
    images: tuple[Image, ...]
    metadata: DocMetadata
    confidence: float


@runtime_checkable
class Extractor(Protocol):
    """One format family.

    Implementations live under ``kairix/extractors/<name>/`` and register
    via the ``kairix.extractors`` entry-point group in ``pyproject.toml``.
    Core code never imports a concrete extractor — only this Protocol.

    Members:

    - ``name`` (``str``): short stable name ("markitdown" | "passthrough"
      | "pdf_fallback" | "ocr" | ...). Matches the entry-point key
      under ``[project.entry-points."kairix.extractors"]``.
    - ``version`` (``str``): plugin version surfaced into
      ``documents_media.extractor_version`` so re-extracts are tractable
      when the plugin bumps (enforced by F40 on each plugin's
      ``__init__.py``).
    - ``can_extract(mime, magic_bytes)``: cheap probe — does this plugin
      claim ownership of the format? Used by the dispatcher to pick the
      right extractor.
    - ``extract(raw, mime)``: produce the ``ExtractedDocument`` from
      raw bytes plus a mime hint. May be CPU-bound (PDF parse, OCR).
    - ``quality_ok(doc)``: post-extraction predicate — is the result
      good enough to ship to Silver, or should the dispatcher escalate
      to a heavier extractor (markitdown → docling / OCR / vision)?
    """

    name: str
    version: str

    def can_extract(self, mime: MimeType, magic_bytes: bytes) -> bool:
        """Return True if this plugin owns the given mime / magic_bytes pair."""

    def extract(self, raw: bytes, mime: MimeType) -> ExtractedDocument:
        """Parse raw bytes into an ``ExtractedDocument``."""

    def quality_ok(self, doc: ExtractedDocument) -> bool:
        """Return True if ``doc`` is good enough to ship to Silver."""


__all__ = [
    "DocMetadata",
    "ExtractedDocument",
    "Extractor",
    "Image",
    "MimeType",
    "Page",
]
