"""Production ``FactStore`` package — SQLite + FTS5 backed.

Public surface (Plan B-parity Week 1):

* ``StoredFactRecord`` — frozen ``FactRecord`` implementation; carries
  the ``mint_id`` deterministic-id helper.
* ``StoredFactHit``   — concrete ``FactHit`` returned by ``search``.
* ``SQLiteFactStore`` — ``FactStore`` Protocol implementation.

Importers (Capability #1 ingest pipeline, the SearchPipeline federation
layer, the future LLM-fact-extractor wiring) should import only from
this package — never from the underlying ``store`` / ``records``
modules — so the F5 boundary stays clean.
"""

from kairix.core.facts.records import StoredFactRecord
from kairix.core.facts.store import SQLiteFactStore, StoredFactHit

__all__ = ["SQLiteFactStore", "StoredFactHit", "StoredFactRecord"]
