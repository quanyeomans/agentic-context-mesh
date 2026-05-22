"""Slide-aware PPTX extractor plugin (Wave 4 OF-1).

Wraps the upstream `python-pptx <https://github.com/scanny/python-pptx>`_
library (MIT, Steve Canny) and adapts it to the
:class:`kairix.extractors.Extractor` Protocol. Markitdown handles PPTX
but flattens the slide structure; this plugin preserves per-slide
chunking + speaker notes for the "cite the specific slide" /
"surface the speaker notes" journeys.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. ``python-pptx`` is declared as an **optional**
dependency (extra ``pptx``) — operators not ingesting presentations
can skip the install and stay on :mod:`kairix.extractors.markitdown`.

.. code-block:: shell

   pip install 'Kairix-agentic-knowledge-mgt[pptx]'

Production callers resolve by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve(
       "application/vnd.openxmlformats-officedocument.presentationml.presentation",
       b"PK\\x03\\x04",
   )
   doc = extractor.extract(raw_bytes, mime)

See ``docs/architecture/connector-ingestion-architecture.md`` §10
(Wave 4 OF-1) + KFEAT-012 Phase 2 §PowerPoint for the ADR and the
ship plan, and ``tests/bdd/features/extractor_pptx.feature`` for the
behaviour spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.pptx.extractor import PLUGIN_NAME, PptxExtractor

#: F40-mandated module-level version. Pinned to the upstream
#: ``python-pptx`` version recorded in the project's lockfile so
#: re-extraction sweeps trigger off a deterministic identifier. Bump
#: in lock-step with ``python-pptx`` upgrades.
#:
#: The F40 detector parses this file's AST and requires a literal
#: string assignment — ``importlib.metadata.version("python-pptx")``
#: or ``pptx.__version__`` at module level is forbidden because it
#: would resolve to whatever is currently installed (which may diverge
#: from the lockfile pin between a security-floor bump and a rebuild).
version: str = "1.0.2"


def make_extractor() -> Extractor:
    """Construct the pptx :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :func:`pptx.Presentation` factory — environments without the
    ``pptx`` extra installed raise a typed ``RuntimeError`` only when
    ``extract`` is actually called, not at module import time. Tests
    pass a synthetic ``presentation_loader=`` to bypass the upstream
    import entirely (F1-clean).
    """
    return PptxExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "PptxExtractor",
    "make_extractor",
    "version",
]
