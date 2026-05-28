@connector @document_pages @gh338
Feature: document_pages writer persists per-page rows for paged extractors
  As an operator running ingest pipelines with mixed page-bearing formats
  I want every extracted page to appear in document_pages with monotonic page numbers
  So that downstream retrieval (MM-3 citation paths) can attribute a chunk back to a specific page

  @happy_path
  Scenario: Paged extractor lands one document_pages row per page
    Given a connector "paged-source" with one binary-pdf change event
    And the configured extractor emits 3 pages with text and alternating has_images
    When the operator runs one pipeline batch for the pages source "paged-source"
    Then 3 document_pages rows exist for that document
    And the page numbers are 1, 2, 3 in ascending order
    And every row has non-empty extracted_text
    And image_descriptions is NULL on every row

  Scenario: Non-paged extractor writes zero document_pages rows
    Given a connector "non-paged-source" with one non-paged change event
    And the configured extractor emits zero pages
    When the operator runs one pipeline batch for the pages source "non-paged-source"
    Then 0 document_pages rows exist
    And the documents_media row still wrote (per-document analytics unaffected)

  Scenario: Re-ingest replaces pages idempotently
    Given a connector "reingest-source" that runs the same item twice
    And the configured extractor emits 3 pages each time
    When the operator runs two consecutive pipeline batches
    Then document_pages still has exactly 3 rows (INSERT OR REPLACE, not append)
