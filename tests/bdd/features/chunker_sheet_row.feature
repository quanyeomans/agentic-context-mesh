@chunker @wave_g1 @sheet_row
Feature: SheetRowChunker emits one chunk per row (or whole sheet for small reference sheets)
  As an operator ingesting Excel sheets via SharePoint or Google Drive
  I want the sheet row chunker to emit one chunk per data row for big tabular sheets
  And to prepend the header row into each chunk
  And to collapse small reference sheets to a single chunk
  So that retrieval keeps column context attached to each row
  And small reference tables retain their cross-row meaning.

  This feature pins the ADR-028 Wave G.1 "XLSX — SheetRowChunker" rule:
  one row IS the unit for large sheets; the whole sheet IS the unit for
  small reference sheets.

  @happy_path
  Scenario: Operator chunks a large tabular sheet via SheetRowChunker
    Given the sheet row chunker is constructed with the default threshold
    And the operator has a scripted sheet with one hundred data rows
    When the operator invokes the sheet row chunker on the sheet markdown
    Then the sheet row chunker emits one chunk per data row
    And each chunk text starts with the header row

  @happy_path
  Scenario: Operator chunks a small reference sheet to a single chunk
    Given the sheet row chunker is constructed with the default threshold
    And the operator has a scripted sheet with twenty data rows
    When the operator invokes the sheet row chunker on the sheet markdown
    Then the sheet row chunker emits exactly one chunk for the whole sheet
