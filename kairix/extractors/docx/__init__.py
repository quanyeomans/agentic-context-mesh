"""docx extractor plugin — heading-hierarchy-aware Word document extraction.

Wraps `python-docx <https://github.com/python-openxml/python-docx>`_
(MIT-licensed, commercial-safe) and adapts it to the
:class:`kairix.extractors.Extractor` Protocol. Targets the
``.docx`` (Office Open XML) format only — legacy ``.doc`` is out
of scope (a separate plugin would route to LibreOffice's
``soffice --convert-to``).

Spec ref: ``docs/architecture/connector-ingestion-architecture.md``
§2 ("extractors tree"), §3 ("Extractor Protocol"), §10 (Wave 4 OF-2)
and KFEAT-012 Phase 2 §Word. This plugin sits beside ``markitdown``
in the dispatch table for ``application/vnd.openxmlformats-
officedocument.wordprocessingml.document`` — the registry resolves
the highest-priority claimant first; operators wanting heading-
hierarchy fidelity register ``docx`` ahead of ``markitdown``.

Heading-hierarchy preservation, lists, tables, and track-changes
acceptance are the four behaviours OF-2 ships. Footnotes ride
along when ``footnotes_part`` is exposed by python-docx (older
versions do not expose this attribute; the plugin degrades silently).

Track-changes handling follows the "accepted version" rule:

  * ``<w:ins>`` content stays in the output (the change has been
    accepted by the extractor).
  * ``<w:del>`` content is skipped (the deletion has been accepted).
  * ``metadata.has_tracked_changes`` is **not** a field on
    :class:`DocMetadata` — instead the plugin records the boolean
    on a sentinel run-time attribute that the registry caller
    inspects only when needed. The dataclass stays frozen; see
    :func:`extract` for the carrier convention.

**Licence ruling**: python-docx is MIT, commercial-safe; no AGPL
contamination. Declared as an *optional* dependency (extra
``docx``) so the core wheel stays light.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. Production callers resolve by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve(
       "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
       b"PK\\x03\\x04",
   )
   doc = extractor.extract(raw_bytes, mime)

See ``tests/bdd/features/extractor_docx.feature`` for the behaviour
spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.docx.extractor import PLUGIN_NAME, DocxExtractor

#: F40-mandated module-level version. Pinned to the python-docx
#: version recorded in the project's lockfile so re-extraction sweeps
#: trigger off a deterministic identifier. Bump in lock-step with
#: python-docx upgrades.
#:
#: The F40 detector parses this file's AST and requires a literal
#: string assignment — ``docx.__version__`` at module level is
#: forbidden because it would resolve to whatever is currently
#: installed (which may diverge from the lockfile pin between a
#: security-floor bump and a rebuild).
version: str = "1.2.0"


def make_extractor() -> Extractor:
    """Construct the docx :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :func:`docx.Document` callable — environments without the
    ``docx`` extra installed raise a typed ``RuntimeError`` only
    when ``extract`` is actually called, not at module import time.
    Tests pass a synthetic ``document_opener=`` to bypass the
    upstream import entirely (F1-clean).
    """
    return DocxExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "DocxExtractor",
    "make_extractor",
    "version",
]
