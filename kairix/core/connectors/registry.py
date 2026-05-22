"""Registry resolvers for connector + extractor plugins.

Plugin discovery is via ``importlib.metadata.entry_points`` (PEP 621
entry-point groups). First-party plugins register in kairix's own
``pyproject.toml``; third parties ship a separate pip distribution
declaring the same entry-point group (see spec doc §8). The
resolver here is the seam consumers depend on - ``factory.build_*``
calls it once at startup to materialise the configured plugin.

Entry-point group names:

  * ``kairix.connectors`` - :class:`~kairix.core.protocols.SourceConnector`
    factories. Each entry-point exposes a ``make_connector`` callable.
  * ``kairix.extractors`` - :class:`~kairix.core.protocols.Extractor`
    factories. Each entry-point exposes a ``make_extractor`` callable.

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons.
"""

from __future__ import annotations

from kairix.core.protocols import Extractor, SourceConnector


class ConnectorRegistry:
    """Resolves a :class:`~kairix.core.protocols.SourceConnector` by name.

    Wave 1 ships the seam-and-shape only; :meth:`resolve` raises
    :class:`NotImplementedError`. Wave 2 (SC-2) lands the real
    ``importlib.metadata.entry_points(group="kairix.connectors", name=name)``
    lookup + ``make_connector(...)`` invocation.
    """

    # resolve(name) -> SourceConnector
    # Wave 2::
    #
    #     eps = importlib.metadata.entry_points(
    #         group="kairix.connectors", name=name
    #     )
    #     make = next(iter(eps)).load()
    #     return make(...)
    #
    # Raises ``KeyError`` if no plugin is registered under ``name``.
    def resolve(self, name: str) -> SourceConnector:
        raise NotImplementedError("ConnectorRegistry.resolve - Wave 2 (SC-1 ships the seam only).")


class ExtractorRegistry:
    """Resolves an :class:`~kairix.core.protocols.Extractor` by mime + magic bytes.

    Wave 1 ships the seam-and-shape only; :meth:`resolve` raises
    :class:`NotImplementedError`. Wave 2 (SC-3) lands the real
    entry-point load plus the can_extract / quality_ok escalation
    chain (markitdown -> pdf_fallback -> ocr -> vision).
    """

    # resolve(mime, magic_bytes) -> Extractor
    # Wave 2: enumerate registered extractor entry-points; return the
    # first one whose
    # :meth:`~kairix.core.protocols.Extractor.can_extract` returns
    # ``True``. Raises ``KeyError`` if no extractor claims the format.
    def resolve(self, mime: str, magic_bytes: bytes) -> Extractor:
        raise NotImplementedError("ExtractorRegistry.resolve - Wave 2 (SC-1 ships the seam only).")
