"""Production ``FactStore`` package — SQLite + FTS5 backed.

Public surface (Plan B-parity Week 1 + Week 2):

* ``StoredFactRecord`` — frozen ``FactRecord`` implementation; carries
  the ``mint_id`` deterministic-id helper.
* ``StoredFactHit``   — concrete ``FactHit`` returned by ``search``.
* ``SQLiteFactStore`` — ``FactStore`` Protocol implementation.
* ``LLMFactExtractor`` — production ``FactExtractor`` (Capability #2)
  that drives a configured ``LLMBackend`` and parses its JSON output
  into ``StoredFactRecord`` instances.

Importers (Capability #1 ingest pipeline, the SearchPipeline federation
layer, the production fact-extractor wire-up) should import only from
this package — never from the underlying ``store`` / ``records`` /
``extractor`` modules — so the F5 boundary stays clean.
"""

from kairix.core.facts.extractor import LLMFactExtractor
from kairix.core.facts.records import StoredFactRecord
from kairix.core.facts.store import SQLiteFactStore, StoredFactHit

__all__ = ["LLMFactExtractor", "SQLiteFactStore", "StoredFactHit", "StoredFactRecord"]
