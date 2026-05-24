@connector @notion @wave-5
Feature: Notion connector pulls workspace pages via the Notion REST API
  As an operator running kairix against a Notion workspace
  I want every change in my configured Notion pages to surface as a typed change event
  So that page content lands in the index with the right source uri and sensitivity
  without me writing per-page-tree glue or maintaining a parallel sync surface.

  The connector reuses the Notion search + block-children pattern
  described in docs/architecture/connector-scope-topology/connector-design-specs/notion.md;
  Markdown is rendered from the block tree, then handed off to the
  kairix extractor registry for downstream processing. The default
  sensitivity tier is internal per spec §1.

  @happy_path
  Scenario: A visible Notion page surfaces as a modified change event
    Given a stubbed Notion REST endpoint that returns one visible page envelope
    When the operator runs the notion connector list_changes with no cursor
    Then one notion modified change event is emitted
    And the notion change event carries an ISO-8601 modified_at timestamp
    And the notion change event's sensitivity tier is internal
    And the notion change event metadata records the source parent type

  @cursor_advance
  Scenario: The connector advances its high-water-mark cursor after a drain
    Given a stubbed Notion REST endpoint that returns one visible page envelope
    When the operator runs the notion connector list_changes with no cursor
    Then the notion connector exposes a non-empty next cursor
    And the notion next cursor matches the highest last_edited_time seen
