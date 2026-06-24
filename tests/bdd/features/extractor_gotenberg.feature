@extractor @gotenberg
Feature: Gotenberg conversion tier converts Office/ODF/Visio/RTF to PDF then re-enters pdf_fallback
  As an operator ingesting legacy Office, OpenDocument, Visio, Publisher, and RTF files
  I want the gotenberg extractor to claim those mime types
  And convert them to PDF via the gotenberg HTTP service
  And route the converted PDF back through the registered pdf_fallback extractor
  So that formats neither markitdown nor pdf_fallback recover natively become indexable
  instead of dead-lettering, while a transient gotenberg outage escalates (raises) for retry
  rather than silently skipping the item.

  This feature pins the PR-3 shape — gotenberg is the convert-then-re-enter
  escalation tier wired between pdf_fallback and ocr. See
  ``docs/architecture/connector-ingestion-architecture.md`` §2 + §3 + §4.

  @happy_path
  Scenario: Operator converts a legacy .doc via gotenberg and re-enters the pdf_fallback tier
    Given the gotenberg extractor is registered under the name "gotenberg"
    And the gotenberg service is configured to return a converted PDF
    And the operator has raw bytes for a legacy office document with a doc mime
    When the operator asks the gotenberg extractor whether it can extract the office mime
    Then the gotenberg extractor claims the mime type
    When the operator invokes the gotenberg extractor's extract method on the bytes
    Then the gotenberg document carries non-empty markdown
    And the gotenberg extractor reports quality_ok true for the produced document
    And the gotenberg extractor's version string is non-empty

  @error
  Scenario: Gotenberg refuses a PDF mime so it never shadows the pdf_fallback tier
    Given the gotenberg extractor is registered under the name "gotenberg"
    When the operator asks the gotenberg extractor whether it can extract mime "application/pdf"
    Then the gotenberg extractor does not claim the mime type

  @error
  Scenario: Gotenberg refuses a modern OOXML docx mime so it never shadows the in-process docx tier
    Given the gotenberg extractor is registered under the name "gotenberg"
    When the operator asks the gotenberg extractor whether it can extract mime "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    Then the gotenberg extractor does not claim the mime type

  @error
  Scenario: Gotenberg refuses a plain text mime so it never shadows passthrough
    Given the gotenberg extractor is registered under the name "gotenberg"
    When the operator asks the gotenberg extractor whether it can extract mime "text/plain"
    Then the gotenberg extractor does not claim the mime type

  @error
  Scenario: Gotenberg refuses an octet-stream mime
    Given the gotenberg extractor is registered under the name "gotenberg"
    When the operator asks the gotenberg extractor whether it can extract mime "application/octet-stream"
    Then the gotenberg extractor does not claim the mime type

  @error
  Scenario: A gotenberg outage raises so the chain escalates to ocr instead of silently skipping
    Given the gotenberg extractor is registered under the name "gotenberg"
    And the gotenberg service is unreachable
    And the operator has raw bytes for a legacy office document with a doc mime
    When the operator invokes the gotenberg extractor's extract method expecting a failure
    Then the gotenberg extractor raises so the orchestrator escalates
