@extractor @wave_4 @docx
Feature: Docx extractor preserves Word heading hierarchy and resolves tracked changes
  As an operator ingesting Word .docx documents
  I want the docx extractor to claim the docx mime type
  And to render Heading 1 / Heading 2 / Heading 3 as markdown #/##/###
  And to render bullet lists, numbered lists, and tables in the expected markdown shapes
  And to handle track-changes by indexing the accepted version
  So that downstream search can rank a Word document's headings as section landmarks
  And the surfaced ExtractedDocument carries the markdown plus a version
  recorded in documents_media.extractor_version.

  This feature pins the OF-2 (Wave 4) shape — docx is the
  heading-hierarchy-aware extractor for Word documents. See
  ``docs/architecture/connector-ingestion-architecture.md`` §2 + §3 +
  §10 (Wave 4 OF-2) and KFEAT-012 Phase 2 §Word.

  @happy_path
  Scenario: Operator extracts a docx with H1/H2/H3 headings, lists, and a table
    Given the docx extractor is registered under the name "docx"
    And the operator has raw bytes for a small docx with three heading levels and one table
    When the operator asks the docx extractor whether it can extract the docx mime
    Then the docx extractor claims the mime type
    When the operator invokes the docx extractor's extract method on the bytes
    Then the docx document carries non-empty markdown
    And the docx markdown contains a heading 1 line
    And the docx markdown contains a heading 2 line
    And the docx markdown contains a heading 3 line
    And the docx markdown contains a bullet list item
    And the docx markdown contains a numbered list item
    And the docx markdown contains a pipe-syntax table row
    And the docx extractor reports quality_ok true for the produced document
    And the docx extractor's version string is non-empty

  @happy_path
  Scenario: Docx claims docx by magic bytes when paired with a docx-shaped mime hint
    Given the docx extractor is registered under the name "docx"
    And the operator has raw bytes whose first four bytes are PK zip magic
    When the operator asks the docx extractor whether it can extract the docx mime
    Then the docx extractor claims the mime type

  @error
  Scenario: Docx refuses a plain text mime type
    Given the docx extractor is registered under the name "docx"
    When the operator asks the docx extractor whether it can extract mime "text/plain"
    Then the docx extractor does not claim the mime type

  @happy_path
  Scenario: Docx indexes the accepted version and flags tracked changes in metadata
    Given the docx extractor is registered under the name "docx"
    And the operator has raw bytes for a docx with inline tracked changes
    When the operator invokes the docx extractor's extract method on the bytes
    Then the docx markdown contains the inserted accepted text
    And the docx markdown does not contain the deleted text
    And the docx extractor flags that tracked changes were present

  @error
  Scenario: Empty markdown from an empty docx fails quality_ok and triggers escalation
    Given the docx extractor is registered under the name "docx"
    And the operator hands docx an essentially empty document body
    When the operator invokes the docx extractor's extract method on the bytes
    Then the docx extractor reports quality_ok false for the produced document
