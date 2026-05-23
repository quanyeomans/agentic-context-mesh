@connector @search @round-trip @im6-regression
Feature: connector-ingested chunks are findable via search
  As an operator running kairix
  I want chunks written by the connector framework to be searchable via BM25
  So that the new connector path is functionally equivalent to the legacy
  DocumentScanner — the precondition for promoting the obsidian_connector_primary
  flag from introduce-stage to cutover-stage.

  Background: this is the regression-pin for the IM-6 cutover gap where the
  ``_SqliteChunkWriter`` wrote to ``documents`` + ``content`` +
  ``content_vectors`` but skipped ``documents_fts``, leaving 68,814 chunks
  in the ``obsidian`` collection invisible to BM25. Hybrid search silently
  degraded to vector-only. The fix landed an FTS5 write on every chunk
  upsert; this feature pins the round-trip behaviour.

  @happy_path
  Scenario: a chunk written by the connector framework is findable by BM25
    Given the connector framework's chunk writer persists a chunk with text "the kairix architecture is layered"
    When BM25 searches for "architecture"
    Then at least one result returns the chunk

  @invariant @fts-vs-documents
  Scenario: every active document the connector wrote has a corresponding FTS row
    Given the connector framework writes 3 chunks across 3 source files
    When the connector batch commits
    Then the count of active documents in the connector's collection equals the count of FTS rows for that collection
