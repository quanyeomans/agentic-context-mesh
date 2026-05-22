"""Markitdown extractor plugin — PDF / DOCX / PPTX / XLSX / HTML default path.

Wraps the upstream `markitdown <https://github.com/microsoft/markitdown>`_
library (MIT, Microsoft) and adapts it to the
:class:`kairix.extractors.Extractor` Protocol. Markitdown is the default
extractor for rich-document formats in Wave 2; the escalation chain
(markitdown → pdf_fallback → ocr → vision) sits above this plugin once
Waves 3-4 ship.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. Markitdown is declared as an **optional** dependency
(extra ``markitdown``) — operators ingesting only markdown / plain-text
files can skip the install and stay on :mod:`kairix.extractors.passthrough`.

.. code-block:: shell

   pip install 'Kairix-agentic-knowledge-mgt[markitdown]'

Production callers resolve by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve("application/pdf", b"%PDF")
   doc = extractor.extract(raw_bytes, "application/pdf")

See ``docs/architecture/connector-ingestion-architecture.md`` §2 + §3
+ §10 (Wave 2 IM-4) for the ADR and the IM-4 ship plan, and
``tests/bdd/features/extractor_markitdown.feature`` for the behaviour
spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.markitdown.extractor import PLUGIN_NAME, MarkitdownExtractor

#: F40-mandated module-level version. Pinned to the markitdown
#: version recorded in the project's lockfile so re-extraction sweeps
#: trigger off a deterministic identifier. Bump in lock-step with
#: ``markitdown`` upgrades.
#:
#: The F40 detector parses this file's AST and requires a literal
#: string assignment — ``importlib.metadata.version("markitdown")``
#: at module level is forbidden because it would resolve to whatever
#: is currently installed (which may diverge from the lockfile pin
#: between a security-floor bump and a rebuild).
version: str = "0.1.5"


def make_extractor() -> Extractor:
    """Construct the markitdown :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :class:`markitdown.MarkItDown` class — environments without the
    ``markitdown`` extra installed raise a typed ``RuntimeError`` only
    when ``extract`` is actually called, not at module import time.
    Tests pass a synthetic ``converter_factory=`` to bypass the
    upstream import entirely (F1-clean).
    """
    return MarkitdownExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "MarkitdownExtractor",
    "make_extractor",
    "version",
]
