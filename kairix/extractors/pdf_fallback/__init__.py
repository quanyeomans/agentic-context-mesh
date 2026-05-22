"""PDF fallback extractor plugin — pdfplumber for content markitdown loses.

Catches PDFs the default :mod:`kairix.extractors.markitdown` plugin
extracts poorly (complex tables, mixed text + image pages). Wraps the
`pdfplumber <https://github.com/jsvine/pdfplumber>`_ library
(MIT-licensed, commercial-safe) and adapts it to the
:class:`kairix.extractors.Extractor` Protocol.

Spec ref: ``docs/architecture/connector-ingestion-architecture.md``
§2 + §3 + §10 (Wave 3 MM-1). The escalation chain wired by the
orchestrator at pipeline-config time is::

    markitdown   (default — Wave 2)
        ↓ quality_ok = False
    pdf_fallback (this plugin — Wave 3 MM-1, pdfplumber MIT)
        ↓ quality_ok = False
    ocr          (MM-2 — Tesseract for image-only PDFs)
        ↓ quality_ok = False
    dead-letter

**Licence ruling (Decision 4)**: pdfplumber is shipped because its MIT
licence makes the kairix distribution commercial-safe. The alternative
``pymupdf`` wraps MuPDF which is AGPL-licensed; shipping it would
force kairix into AGPL territory, which the operator deployments in
scope cannot accept. pymupdf is **not** part of the plugin's
implementation or test surface.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. pdfplumber is declared as an **optional**
dependency (extra ``pdf_fallback``); operators on
``markitdown[pdf]`` already get pdfplumber transitively, so the
fallback is available for free in the default rich-document install.

.. code-block:: shell

   pip install 'Kairix-agentic-knowledge-mgt[pdf_fallback]'

Production callers resolve by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve("application/pdf", b"%PDF")
   doc = extractor.extract(raw_bytes, "application/pdf")

See ``tests/bdd/features/extractor_pdf_fallback.feature`` for the
behaviour spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.pdf_fallback.extractor import (
    PLUGIN_NAME,
    PdfFallbackExtractor,
)

#: F40-mandated module-level version. Pinned to the pdfplumber version
#: recorded in the project's lockfile so re-extraction sweeps trigger
#: off a deterministic identifier. Bump in lock-step with pdfplumber
#: upgrades.
#:
#: The F40 detector parses this file's AST and requires a literal
#: string assignment — ``pdfplumber.__version__`` at module level is
#: forbidden because it would resolve to whatever is currently
#: installed (which may diverge from the lockfile pin between a
#: security-floor bump and a rebuild).
version: str = "0.11.9"


def make_extractor() -> Extractor:
    """Construct the pdf_fallback :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :func:`pdfplumber.open` callable — environments without the
    ``pdf_fallback`` extra installed raise a typed ``RuntimeError``
    only when ``extract`` is actually called, not at module import
    time. Tests pass a synthetic ``pdf_opener=`` to bypass the
    upstream import entirely (F1-clean).
    """
    return PdfFallbackExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "PdfFallbackExtractor",
    "make_extractor",
    "version",
]
