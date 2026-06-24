"""gotenberg extractor plugin — Office/ODF/Visio/Publisher/RTF → PDF conversion tier.

Converts the formats neither ``markitdown`` nor ``pdf_fallback`` can
recover (legacy ``.doc`` / ``.xls`` / ``.ppt``, the OpenDocument
family, Visio drawings, Microsoft Publisher, RTF) to PDF via the
`gotenberg <https://gotenberg.dev>`_ HTTP service, then routes the
converted PDF back through the registered ``pdf_fallback`` extractor so
the document inherits its table extraction + per-page chunks. Adapts the
conversion to the :class:`kairix.extractors.Extractor` Protocol.

The plugin sits between ``pdf_fallback`` and ``ocr`` in the escalation
chain wired by the orchestrator at pipeline-config time::

    markitdown   (default)
        ↓ can_extract = False (not a format it claims)
    pdf_fallback (pdfplumber — claims only PDFs)
        ↓ can_extract = False (the source is a .doc / .odt / .vsdx)
    gotenberg    (this plugin — convert-then-re-enter pdf_fallback)
        ↓ extract raised (gotenberg outage / 4xx)
    ocr          (Tesseract for image-only PDFs)

When gotenberg is unreachable / times out / returns an empty or 4xx
response, :meth:`extract` RAISES so the chain escalates to ``ocr`` and,
on exhaustion, the item dead-letters for retry — a transient gotenberg
outage is never a silent skip.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. ``httpx`` (the HTTP client) and ``tenacity`` (the
retry primitive) are both base kairix dependencies, so no optional
extra is required — the plugin only needs a reachable gotenberg service
at convert time.

.. code-block:: python

   from kairix.core.connectors.registry import build_extractor_from_entry

   chain = build_extractor_from_entry(
       {
           "extractor_chain": ["markitdown", "pdf_fallback", "gotenberg", "ocr"],
           "extractor_chain_configs": {
               "gotenberg": {"config": {"gotenberg_url": "http://gotenberg:3000"}},
           },
       }
   )

Spec ref: ``docs/architecture/connector-ingestion-architecture.md`` §2
+ §3 + §4 and PR-3.
"""

from __future__ import annotations

from typing import Any

from kairix.extractors import Extractor
from kairix.extractors.gotenberg.extractor import (
    PLUGIN_NAME,
    GotenbergExtractor,
    GotenbergExtractorConfig,
)

#: F40-mandated module-level version. Pinned to a literal so the F40 AST
#: detector sees a constant string assignment — re-extraction sweeps
#: trigger off this deterministic identifier. Bump when the conversion
#: behaviour changes (gotenberg route / LibreOffice fidelity shift) so
#: ``documents_media.extractor_version`` flags affected derivatives.
version: str = "1.0.0"


def make_extractor(*, config: GotenbergExtractorConfig | dict[str, Any] | None = None) -> Extractor:
    """Construct the gotenberg :class:`Extractor` for entry-point discovery.

    When ``config`` is ``None``, the gotenberg URL / timeout / size
    ceiling default to their literals unless the operator has set the
    ``KAIRIX_GOTENBERG_*`` env vars — those are read at the F4 boundary
    (:func:`kairix.paths.gotenberg_extractor_config`) so this module
    never touches ``os.environ`` directly. Operators override per
    connector via ``extractor_chain_configs.gotenberg.config``.

    The registry resolves per-member YAML and calls this factory as
    ``make_extractor(**{"config": {"gotenberg_url": ...}})`` — i.e.
    ``config`` arrives as a raw ``dict``, not a
    :class:`GotenbergExtractorConfig`. Coerce it here so the documented
    ``extractor_chain_configs.gotenberg.config`` YAML produces a real
    config object (otherwise the first ``extract`` raises
    ``AttributeError`` on ``self._config.max_file_size_mb``).

    The converted PDF re-enters the registered ``pdf_fallback`` tier,
    lazy-resolved via the registry on first ``extract`` (tests inject a
    fake ``pdf_extractor=`` instead).
    """
    resolved: GotenbergExtractorConfig
    if config is None:
        from kairix.paths import gotenberg_extractor_config

        resolved = gotenberg_extractor_config()
    elif isinstance(config, dict):
        resolved = GotenbergExtractorConfig(**config)
    else:
        resolved = config
    return GotenbergExtractor(version=version, config=resolved)


__all__ = [
    "PLUGIN_NAME",
    "GotenbergExtractor",
    "GotenbergExtractorConfig",
    "make_extractor",
    "version",
]
