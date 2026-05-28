@connector @documents_media @gh336
Feature: documents_media writer surfaces per-extractor + per-document outcome
  As an operator running ingest pipelines
  I want every processed document to appear in documents_media with extractor identity and status
  So that F40 re-extract triage, per-extractor analytics, and the canonical bronze -> media -> content
   join return rows that match what was actually processed

  Scenario: Happy path lands an ok-status row with extractor identity
    Given a connector "happy-source" with one markdown change event
    And the configured extractor is the canonical FakeExtractor
    When the operator runs one pipeline batch for "happy-source"
    Then a documents_media row exists with extraction_status "ok"
    And that row carries the extractor name "fake-extractor"
    And that row carries the extractor version "0.0.0"

  Scenario: Failure path lands a failed-status row alongside the dead-letter entry
    Given a connector "failed-source" with one corrupt-PDF change event
    And the configured extractor raises on extract
    When the operator runs one pipeline batch for "failed-source"
    Then a documents_media row exists with extraction_status "failed"
    And that row carries the failing extractor identity
    And the item appears in the connector_deadletter table

  Scenario: Unsupported quality lands an unsupported-status row
    Given a connector "unsupported-source" with one video-like change event
    And the configured extractor reports quality_ok=False
    When the operator runs one pipeline batch for "unsupported-source"
    Then a documents_media row exists with extraction_status "unsupported"
    And the item does NOT appear in the connector_deadletter table
