@extractor @wave_4 @xlsx
Feature: Xlsx extractor renders each worksheet as a markdown page
  As an operator ingesting Excel spreadsheets
  I want the xlsx extractor to claim the spreadsheetml.sheet mime
  And to render each non-empty worksheet as its own Page in the ExtractedDocument
  And to skip empty / chart-only sheets so they don't dilute retrieval
  And to collapse merged cells to the top-left value
  And to resolve formula cells to their cached displayed value (not the formula text)
  So that downstream chunking can cite a specific sheet
  And the surfaced ExtractedDocument carries one markdown section per sheet plus a
  module-level version recorded in documents_media.extractor_version.

  This feature pins the OF-3 (Wave 4) shape — xlsx is the sheet-as-
  document extractor for Office mixed-media ingest. See
  ``docs/architecture/connector-ingestion-architecture.md`` §10
  (Wave 4 OF-3).

  @happy_path
  Scenario: Operator extracts an xlsx with three sheets and gets two pages back
    Given the xlsx extractor is registered under the name "xlsx"
    And the operator has an xlsx workbook with sheets named "Data" and "Empty" and "Charts"
    When the operator asks the xlsx extractor whether it can extract the spreadsheetml.sheet mime
    Then the xlsx extractor claims the mime type
    When the operator invokes the xlsx extractor's extract method on the workbook bytes
    Then the xlsx document carries one page per non-empty sheet
    And the xlsx document markdown carries a "## Sheet: Data" header
    And the xlsx document markdown carries a "## Sheet: Charts" header
    And the xlsx document markdown does not carry a "## Sheet: Empty" header
    And the xlsx extractor reports quality_ok true for the produced document
    And the xlsx extractor's version string is non-empty

  @happy_path
  Scenario: Xlsx claims the spreadsheetml.sheet mime
    Given the xlsx extractor is registered under the name "xlsx"
    When the operator asks the xlsx extractor whether it can extract the spreadsheetml.sheet mime
    Then the xlsx extractor claims the mime type

  @happy_path
  Scenario: Merged cells collapse to the top-left value
    Given the xlsx extractor is registered under the name "xlsx"
    And the operator has an xlsx workbook whose top-left cell spans the row across three columns
    When the operator invokes the xlsx extractor's extract method on the workbook bytes
    Then the merged cell value appears exactly once in the rendered markdown

  @error
  Scenario: Xlsx refuses a plain text mime type
    Given the xlsx extractor is registered under the name "xlsx"
    When the operator asks the xlsx extractor whether it can extract mime "text/plain"
    Then the xlsx extractor does not claim the mime type

  @error
  Scenario: An all-empty workbook fails quality_ok and triggers escalation
    Given the xlsx extractor is registered under the name "xlsx"
    And the operator has an xlsx workbook with one empty sheet
    When the operator invokes the xlsx extractor's extract method on the workbook bytes
    Then the xlsx extractor reports quality_ok false for the produced document
