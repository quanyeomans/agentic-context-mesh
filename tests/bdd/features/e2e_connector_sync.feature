@connector @e2e @wave-1-stub
Feature: End-to-end connector sync journey
  As an operator deploying kairix against any supported external source
  I want a configured connector and extractor pair to ingest fixture content
  So that documents flow into the index with source uri and sensitivity populated
  and adding a new plugin is one Examples row, not a copy-pasted feature.

  This is a Scenario Outline parameterised over connectors and extractors.
  Wave 1 ships the empty (placeholder) Examples table; Wave 2 lands the
  first real row (obsidian plus markitdown). F36 enforces row-per-plugin
  so the surface stays mechanical to audit.

  @placeholder
  Scenario Outline: Operator configures connector <connector> with extractor <extractor> and ingests fixture
    Given the operator has configured a connector named "<connector>"
    And the operator has registered an extractor named "<extractor>"
    And the operator has placed a fixture under the connector's source root
    When the operator runs the connector sync from the command line
    Then the fixture lands in the silver index with a populated source uri
    And the fixture lands in the silver index with a populated sensitivity tier
    And the cursor for the connector advances past the fixture

    Examples: First-party connector and extractor pairs
      | connector | extractor    |
      | obsidian  | passthrough  |
      | obsidian  | markitdown   |
      | obsidian  | pdf_fallback |
      | obsidian  | ocr          |
      | obsidian  | pptx         |
      | obsidian  | docx         |
      | obsidian  | xlsx         |
