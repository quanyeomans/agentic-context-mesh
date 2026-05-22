@extractor @wave_2 @markitdown
Feature: Markitdown extractor converts rich documents to markdown
  As an operator ingesting PDF / DOCX / PPTX / XLSX / HTML content
  I want the markitdown extractor to claim those mime types
  And to delegate the conversion to the upstream markitdown library
  And to escalate via quality_ok when the conversion recovers too few bytes
  So that scanned PDFs (image-only) fall through to OCR in Wave 3
  And the surfaced ExtractedDocument carries the converted markdown plus a version
  recorded in documents_media.extractor_version.

  This feature pins the IM-4 (Wave 2) shape — markitdown is the
  default extractor for the rich-document escalation chain. See
  ``docs/architecture/connector-ingestion-architecture.md`` §2 + §3 +
  §4 (escalation gate) + §10 (Wave 2 IM-4).

  @happy_path
  Scenario: Operator extracts a PDF via the markitdown plugin
    Given the markitdown extractor is registered under the name "markitdown"
    And the operator has raw bytes for a small PDF with text content
    When the operator asks the markitdown extractor whether it can extract mime "application/pdf"
    Then the markitdown extractor claims the mime type
    When the operator invokes the markitdown extractor's extract method on the bytes
    Then the markitdown document carries non-empty markdown
    And the markitdown extractor reports quality_ok true for the produced document
    And the markitdown extractor's version string is non-empty

  @happy_path
  Scenario: Markitdown claims a PDF by magic bytes when the mime is generic
    Given the markitdown extractor is registered under the name "markitdown"
    And the operator has raw bytes whose first four bytes are "%PDF"
    When the operator asks the markitdown extractor whether it can extract mime "application/octet-stream"
    Then the markitdown extractor claims the mime type

  @happy_path
  Scenario: Markitdown claims DOCX and PPTX and XLSX mime types
    Given the markitdown extractor is registered under the name "markitdown"
    When the operator asks the markitdown extractor whether it can extract the office open xml document mime
    Then the markitdown extractor claims the mime type
    When the operator asks the markitdown extractor whether it can extract the office open xml presentation mime
    Then the markitdown extractor claims the mime type
    When the operator asks the markitdown extractor whether it can extract the office open xml spreadsheet mime
    Then the markitdown extractor claims the mime type

  @error
  Scenario: Markitdown refuses a plain text mime type
    Given the markitdown extractor is registered under the name "markitdown"
    When the operator asks the markitdown extractor whether it can extract mime "text/plain"
    Then the markitdown extractor does not claim the mime type

  @error
  Scenario: Empty markdown from a scanned-PDF fails quality_ok and triggers escalation
    Given the markitdown extractor is registered under the name "markitdown"
    And the upstream converter returns empty markdown for the supplied bytes
    When the operator invokes the markitdown extractor's extract method on the bytes
    Then the markitdown extractor reports quality_ok false for the produced document
