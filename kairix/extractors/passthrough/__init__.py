"""Passthrough extractor plugin — ``text/markdown`` / ``text/plain`` no-op.

The simplest first-party :class:`kairix.extractors.Extractor`. Used by
Obsidian-style sources whose native format is already markdown — no
conversion is required, only a UTF-8 decode and a wrap in the standard
:class:`ExtractedDocument` value object.

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. Production callers resolve it by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve("text/markdown", b"# header\\n")
   doc = extractor.extract(raw_bytes, "text/markdown")

Passthrough has no upstream library to track, so the F40-mandated
``version`` is a hand-managed semver — bump it whenever the plugin's
behaviour changes in a way that should trigger re-extraction of
prior documents.

See ``docs/architecture/connector-ingestion-architecture.md`` §2 +
§3 + §10 (Wave 2 IM-4) for the ADR and the IM-4 ship plan, and
``tests/bdd/features/extractor_passthrough.feature`` for the
behaviour spec this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.passthrough.extractor import PLUGIN_NAME, PassthroughExtractor

#: F40-mandated module-level version. The passthrough extractor has no
#: upstream library version to track; this string is the canonical
#: signal recorded in ``documents_media.extractor_version`` for every
#: document this plugin produces. Bump on behaviour changes that
#: warrant re-extraction of prior documents.
version: str = "1.0.0"


def make_extractor() -> Extractor:
    """Construct the passthrough :class:`Extractor` for entry-point discovery.

    No credential or transport resolution is needed — passthrough has
    no upstream dependency. The constructor receives ``version=`` from
    this module's :data:`version` so the F40 declaration site stays
    canonical (the class doesn't hard-code the same string).
    """
    return PassthroughExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "PassthroughExtractor",
    "make_extractor",
    "version",
]
