@feature_flag @obsidian_connector_primary
Feature: Operator toggles the obsidian-connector-primary feature flag
  As an operator running a kairix engagement container
  I want to choose between the legacy document scanner and the new obsidian connector
  So that I can validate the new ingest path before cutting over and roll back safely if needed

  Both code paths stay present until the flag retires; the operator can
  flip the flag back at any time without losing indexed content. See
  docs/architecture/feature-flag-architecture.md §7 for the IM-6 recast.

  @happy_path @off
  Scenario: Flag OFF — the legacy document scanner indexes the document store
    Given the operator has the obsidian-connector-primary flag set to false
    When the worker connector sync tick runs
    Then the legacy document scanner branch performs the indexing pass
    And the obsidian connector pipeline branch does not run

  @happy_path @on
  Scenario: Flag ON — the obsidian connector pipeline indexes the document store
    Given the operator has the obsidian-connector-primary flag set to true
    When the worker connector sync tick runs
    Then the obsidian connector pipeline branch performs the indexing pass
    And the legacy document scanner branch does not run
