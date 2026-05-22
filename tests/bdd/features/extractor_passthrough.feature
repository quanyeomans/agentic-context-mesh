@extractor @wave_2 @passthrough
Feature: Passthrough extractor surfaces markdown bytes as ExtractedDocument
  As an operator running the connector pipeline against a markdown-native source
  I want the passthrough extractor to claim text/markdown and text/plain mime types
  And to decode the raw bytes as UTF-8 without conversion
  So that Obsidian-style content flows into the silver index unchanged
  And the extractor declares quality_ok only when the document has visible content.

  This feature pins the IM-4 (Wave 2) shape — the simplest extractor
  in the registry, used as the canonical reference for the
  Extractor Protocol. See
  ``docs/architecture/connector-ingestion-architecture.md`` §3.

  @happy_path
  Scenario: Operator extracts a markdown note via the passthrough plugin
    Given the passthrough extractor is registered under the name "passthrough"
    And the operator has raw bytes for a markdown note that decode to non-empty UTF-8
    When the operator asks the passthrough extractor whether it can extract mime "text/markdown"
    Then the passthrough extractor claims the mime type
    When the operator invokes the passthrough extractor's extract method on the bytes
    Then the passthrough document carries the decoded markdown
    And the passthrough document has an empty pages tuple
    And the passthrough document has an empty images tuple
    And the passthrough extractor reports quality_ok true for the produced document

  @happy_path
  Scenario: Plain text bytes round-trip through the passthrough extractor
    Given the passthrough extractor is registered under the name "passthrough"
    And the operator has plain text bytes that decode to non-empty UTF-8
    When the operator asks the passthrough extractor whether it can extract mime "text/plain"
    Then the passthrough extractor claims the mime type
    When the operator invokes the passthrough extractor's extract method on the bytes
    Then the passthrough document carries the decoded text

  @error
  Scenario: Passthrough refuses a binary mime type and never falsely claims PDF
    Given the passthrough extractor is registered under the name "passthrough"
    When the operator asks the passthrough extractor whether it can extract mime "application/pdf"
    Then the passthrough extractor does not claim the mime type

  @error
  Scenario: Empty bytes produce a document but quality_ok is false
    Given the passthrough extractor is registered under the name "passthrough"
    And the operator has empty raw bytes
    When the operator invokes the passthrough extractor's extract method on the bytes
    Then the passthrough extractor reports quality_ok false for the produced document
