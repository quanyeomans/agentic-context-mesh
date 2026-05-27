@connector @metadata @wave-e5
Feature: Per-source envelope metadata reaches the chunk
  As an operator who searches across SharePoint / GitHub / Slack / Notion
  I want each chunk to carry the envelope author + last-modified-at + tags
  So that temporal-boost search and the entity graph cover every source

  Scenario: Envelope author lands on the chunk
    Given a configured connector emitting one item with envelope author "agent-alpha"
    When the operator runs one pipeline batch through the factory
    Then the indexed chunk carries the author "agent-alpha"
    And the indexed chunk carries the envelope's modified-at as the chunk date

  Scenario: Connector envelope wins over extractor body on author collision
    Given a configured connector emitting one item with envelope author "envelope-author"
    And the configured extractor surfaces document-body author "body-author"
    When the operator runs one pipeline batch through the factory
    Then the indexed chunk carries the author "envelope-author"
