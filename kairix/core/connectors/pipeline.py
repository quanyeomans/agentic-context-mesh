"""ConnectorPipeline - the per-batch orchestrator that composes the connector framework.

One canonical pipeline. Connectors and extractors plug in via their
respective Protocols (:class:`~kairix.core.protocols.SourceConnector`,
:class:`~kairix.core.protocols.Extractor`); the pipeline itself knows
nothing about specific sources or formats.

Pipeline body (spec doc §4 - Wave 2 implements):

  for change in connector.list_changes(cursor):
      raw = connector.fetch(change.item_id)
      ref = bronze.write(connector.name, change.item_id, raw.raw, raw.mime)
      extractor = registry.resolve(raw.mime, raw.raw[:8])
      doc = extractor.extract(raw.raw, raw.mime)
      if not extractor.quality_ok(doc):
          doc = escalate(doc, raw)
      silver_out = silver.process(
          ref, doc,
          source_uri=connector.source_link(change.item_id),
          source_modified_at=change.modified_at,
          sensitivity=connector.sensitivity_for(change.item_id),
      )
      documents_writer.upsert(silver_out.chunks)
      entity_graph_sink.stage(silver_out.entity_signals)
      cursor_store.advance(connector.name, change.cursor_token)

All of the above runs inside ONE SQLite transaction per batch. On any
failure the transaction rolls back and the cursor stays where it was;
the batch is retried on the next worker tick. Three failure modes map
to three behaviours (see spec doc §4 - fetch / extract / silver).

Method bodies are intentionally single-statement
``raise NotImplementedError`` calls so the F19 unused-parameter rule
recognises them as abstract-style skeletons.
"""

from __future__ import annotations

from kairix.core.protocols import Extractor, SourceConnector


class ConnectorPipeline:
    """Production connector orchestrator.

    Wave 1 ships the seam-and-shape only; :meth:`run_batch` raises
    :class:`NotImplementedError`. Wave 2 (IM-2) lands the per-batch
    transaction body - see module docstring for the canonical
    sequence.
    """

    # run_batch(connector, extractor) -> int
    # Wave 2: drive one batch of changes for ``connector`` through to
    # indexed chunks. Returns the count of items successfully
    # processed (excludes dead-lettered items). The connector's cursor
    # advances ONLY on successful batch commit; partial batches roll
    # back and the cursor stays where it was so the next tick retries
    # the same range.
    def run_batch(self, connector: SourceConnector, extractor: Extractor) -> int:
        raise NotImplementedError("ConnectorPipeline.run_batch - Wave 2 (SC-1 ships the seam only).")
