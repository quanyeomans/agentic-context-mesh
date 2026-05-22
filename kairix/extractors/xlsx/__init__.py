"""xlsx extractor plugin — sheet-as-document extraction for Excel files.

Wraps the upstream `openpyxl <https://openpyxl.readthedocs.io>`_ library
(MIT, commercial-safe) and adapts it to the
:class:`kairix.extractors.Extractor` Protocol. Wave 4 OF-3 — every non-
empty / non-chart-only worksheet becomes its own :class:`Page` in the
returned :class:`ExtractedDocument`; tables render with pipe-syntax
markdown; merged cells collapse to the top-left value; formula cells
resolve to their cached displayed value (``data_only=True``).

The plugin is discovered by the extractor registry through the
``[project.entry-points."kairix.extractors"]`` table in kairix's
``pyproject.toml``. openpyxl is declared as an **optional** dependency
(extra ``xlsx``) — operators not ingesting spreadsheets skip the install.

.. code-block:: shell

   pip install 'Kairix-agentic-knowledge-mgt[xlsx]'

Production callers resolve by name:

.. code-block:: python

   from kairix.core.connectors.registry import ExtractorRegistry

   extractor = ExtractorRegistry().resolve(
       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
       b"PK",
   )
   doc = extractor.extract(raw_bytes, "...")

See ``docs/architecture/connector-ingestion-architecture.md`` §10
(Wave 4 OF-3) for the ADR and
``tests/bdd/features/extractor_xlsx.feature`` for the behaviour spec
this plugin satisfies.
"""

from __future__ import annotations

from kairix.extractors import Extractor
from kairix.extractors.xlsx.extractor import PLUGIN_NAME, XlsxExtractor

#: F40-mandated module-level version. Pinned to the openpyxl version
#: recorded in the project's lockfile so re-extraction sweeps trigger
#: off a deterministic identifier. Bump in lock-step with ``openpyxl``
#: upgrades.
#:
#: The F40 detector parses this file's AST and requires a literal
#: string assignment — ``importlib.metadata.version("openpyxl")``
#: at module level is forbidden because it would resolve to whatever
#: is currently installed (which may diverge from the lockfile pin).
version: str = "3.1.5"


def make_extractor() -> Extractor:
    """Construct the xlsx :class:`Extractor` for entry-point discovery.

    The default constructor lazy-imports the upstream
    :mod:`openpyxl` module inside :meth:`XlsxExtractor.extract` —
    environments without the ``xlsx`` extra installed raise a typed
    ``RuntimeError`` only when ``extract`` is actually called, not at
    module import time. Tests pass a synthetic ``workbook_loader=``
    to bypass the upstream import entirely (F1-clean).
    """
    return XlsxExtractor(version=version)


__all__ = [
    "PLUGIN_NAME",
    "XlsxExtractor",
    "make_extractor",
    "version",
]
