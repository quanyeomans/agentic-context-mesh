@extractor @wave_3 @pdf_fallback
Feature: PDF fallback extractor recovers content markitdown loses
  As an operator ingesting PDFs whose tables or scanned pages defeat the default extractor
  I want the pdf_fallback extractor to claim application/pdf content
  And to render extracted text plus tables as markdown with per-page citations
  And to escalate via quality_ok when the PDF carries no text layer
  So that scanned (image-only) PDFs continue down the chain to OCR
  And the surfaced ExtractedDocument carries pages a chunker can cite back to.

  This feature pins the MM-1 (Wave 3) shape — pdf_fallback is the
  second hop in the escalation chain (markitdown -> pdf_fallback ->
  ocr). pdfplumber (MIT-licensed) is the upstream library; pymupdf
  (AGPL) is explicitly NOT shipped. See
  ``docs/architecture/connector-ingestion-architecture.md`` §2 + §3 +
  §4 (escalation gate) + §10 (Wave 3 MM-1).

  @happy_path
  Scenario: Operator extracts a text-bearing PDF via the pdf_fallback plugin
    Given the pdf_fallback extractor is registered under the name "pdf_fallback"
    And the operator has raw bytes for a small PDF with a text content stream
    When the operator asks the pdf_fallback extractor whether it can extract mime "application/pdf"
    Then the pdf_fallback extractor claims the mime type
    When the operator invokes the pdf_fallback extractor's extract method on the bytes
    Then the pdf_fallback document carries non-empty markdown
    And the pdf_fallback document carries at least one page with non-empty text
    And the pdf_fallback extractor reports quality_ok true for the produced document
    And the pdf_fallback extractor's version string is non-empty

  @happy_path
  Scenario: pdf_fallback claims a PDF by magic bytes when the mime is generic
    Given the pdf_fallback extractor is registered under the name "pdf_fallback"
    And the operator hands pdf_fallback raw bytes whose first four bytes are "%PDF"
    When the operator asks the pdf_fallback extractor whether it can extract mime "application/octet-stream"
    Then the pdf_fallback extractor claims the mime type

  @error
  Scenario: pdf_fallback refuses a non-PDF mime type
    Given the pdf_fallback extractor is registered under the name "pdf_fallback"
    When the operator asks the pdf_fallback extractor whether it can extract mime "text/plain"
    Then the pdf_fallback extractor does not claim the mime type

  @error
  Scenario: An image-only PDF fails quality_ok and escalates to OCR
    Given the pdf_fallback extractor is registered under the name "pdf_fallback"
    And the upstream pdfplumber returns empty page text for the supplied bytes
    When the operator invokes the pdf_fallback extractor's extract method on the bytes
    Then the pdf_fallback extractor reports quality_ok false for the produced document
