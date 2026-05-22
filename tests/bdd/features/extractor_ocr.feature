@extractor @wave_3 @ocr
Feature: OCR extractor recovers text from scanned PDFs and image-only pages
  As an operator ingesting scanned documents and image-only PDFs
  I want the OCR extractor to claim those mime types
  And to run a pre-processing chain plus Tesseract on each page
  And to escalate via quality_ok when recognition confidence falls below the floor
  So that low-confidence pages route to the vision LLM in Phase 3
  And the surfaced ExtractedDocument carries the recognised markdown plus a version
  recorded in documents_media.extractor_version.

  This feature pins the MM-2 (Wave 3) shape — OCR is the third
  member of the escalation chain (markitdown → pdf_fallback → ocr
  → vision). See
  ``docs/architecture/connector-ingestion-architecture.md`` §10 +
  ``KFEAT-012 Addendum`` for the pre-processing chain spec.

  @happy_path
  Scenario: Operator extracts a scanned PDF via the OCR plugin
    Given the ocr extractor is registered under the name "ocr"
    And the operator has raw bytes for a scanned PDF with one page of text
    When the operator asks the ocr extractor whether it can extract mime "application/pdf"
    Then the ocr extractor claims the mime type
    When the operator invokes the ocr extractor's extract method on the bytes
    Then the ocr document carries non-empty markdown
    And the ocr extractor reports quality_ok true for the produced document
    And the ocr extractor's version string is non-empty

  @happy_path
  Scenario: OCR claims a PDF by magic bytes when the mime is generic
    Given the ocr extractor is registered under the name "ocr"
    And the operator has raw scanned bytes whose first four bytes are "%PDF"
    When the operator asks the ocr extractor whether it can extract mime "application/octet-stream"
    Then the ocr extractor claims the mime type

  @happy_path
  Scenario: OCR claims the common image mime types
    Given the ocr extractor is registered under the name "ocr"
    When the operator asks the ocr extractor whether it can extract mime "image/png"
    Then the ocr extractor claims the mime type
    When the operator asks the ocr extractor whether it can extract mime "image/jpeg"
    Then the ocr extractor claims the mime type
    When the operator asks the ocr extractor whether it can extract mime "image/tiff"
    Then the ocr extractor claims the mime type

  @error
  Scenario: OCR refuses a plain text mime type
    Given the ocr extractor is registered under the name "ocr"
    When the operator asks the ocr extractor whether it can extract mime "text/plain"
    Then the ocr extractor does not claim the mime type

  @error
  Scenario: Low-confidence recognition fails quality_ok and triggers vision escalation
    Given the ocr extractor is registered under the name "ocr"
    And the upstream tesseract runner reports low confidence for the supplied bytes
    When the operator invokes the ocr extractor's extract method on the bytes
    Then the ocr extractor reports quality_ok false for the produced document
