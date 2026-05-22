"""OCR extractor plugin — scanned PDFs + image-only pages.

The third member of the escalation chain
(``markitdown`` → ``pdf_fallback`` → ``ocr`` → vision). When the
upstream pipeline reports ``quality_ok() = False`` for both
``markitdown`` and ``pdf_fallback`` (markitdown returned ~zero bytes
on an image-only PDF; pdfplumber returned empty / low-confidence
text), the orchestrator routes the raw bytes here.

This plugin wraps the Apache-2.0 Tesseract C++ engine via the
Apache-2.0 :mod:`pytesseract` Python binding. PaddleOCR (decision 5)
is the opt-in escalation step beyond this plugin and ships as a
separate extractor — not in MM-2 scope.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. Production callers resolve it by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve("application/pdf", b"%PDF")
   doc = extractor.extract(scanned_pdf_bytes, "application/pdf")

Tesseract is a **runtime dependency** that must be present on the
host (``brew install tesseract`` on macOS, ``apt install tesseract-ocr``
on Debian/Ubuntu). The Python-side requirements
(``pytesseract``, ``opencv-python-headless``, ``Pillow``,
``pdfplumber``) install via the ``ocr`` extra:

.. code-block:: shell

   pip install 'Kairix-agentic-knowledge-mgt[ocr]'

See ``docs/architecture/connector-ingestion-architecture.md`` §10
(Wave 3 MM-2) for the ADR and the wave plan, and
``tests/bdd/features/extractor_ocr.feature`` for the behaviour
spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.ocr.extractor import PLUGIN_NAME, OcrExtractor

#: F40-mandated module-level version. The OCR plugin's behaviour is a
#: composition of (a) the upstream Tesseract engine and (b) the
#: pre-processing chain in :mod:`kairix.extractors.ocr.preprocess`.
#: Bump in lockstep with any behaviour change in either layer
#: (preprocessing-step add / remove, default-PSM change, confidence
#: aggregation change) so re-extraction sweeps trigger off a
#: deterministic identifier.
version: str = "1.0.0"


def make_extractor() -> Extractor:
    """Construct the OCR :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :mod:`pytesseract` package — environments without the ``ocr``
    extra installed raise a typed ``RuntimeError`` only when
    ``extract`` is actually called, not at module import time. Tests
    pass synthetic ``page_renderer=`` / ``ocr_runner=`` arguments to
    bypass the upstream import entirely (F1-clean).
    """
    return OcrExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "OcrExtractor",
    "make_extractor",
    "version",
]
