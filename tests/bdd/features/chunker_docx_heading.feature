@chunker @wave_g1 @docx_heading
Feature: DocxHeadingChunker splits on heading hierarchy and emits tables separately
  As an operator ingesting Word documents via SharePoint or Google Drive
  I want the docx heading chunker to split on H1 / H2 / H3 boundaries
  And to tag each chunk with its inherited section path breadcrumb
  And to emit embedded tables as their own chunks
  So that retrieval cites back to the section a passage belongs to
  And tables stay queryable as tables instead of being linearised as prose.

  This feature pins the ADR-028 Wave G.1 "DOCX — DocxHeadingChunker" rule:
  heading hierarchy IS the split boundary; tables are first-class chunks.

  @happy_path
  Scenario: Operator chunks a heading-structured doc with one embedded table
    Given the docx heading chunker is constructed
    And the operator has a scripted docx markdown with hierarchy and a table
    When the operator invokes the docx heading chunker on the docx markdown
    Then the docx heading chunker emits at least one prose chunk per section
    And the docx heading chunker emits a separate chunk for the embedded table
    And each chunk carries its section path in metadata
