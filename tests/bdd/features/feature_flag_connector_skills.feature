@feature_flag @connector_skills
Feature: Operator toggles the skills connector feature flag
  As an operator running a kairix deployment
  I want to choose whether the skills connector is enabled
  So that I can validate the capability-recommender ingest path before cutting over and roll back safely if needed

  The flag gates the connector at the dispatch boundary. When OFF the
  connector slot is a no-op (zero counters); when ON the standard
  connector pipeline resolves the skills plugin via its entry-point and
  walks the host's ~/.claude tree. See docs/architecture/feature-flag-architecture.md section 7.

  @happy_path @off
  Scenario: Flag OFF — the skills connector slot is a no-op
    Given the operator has the skills connector flag set to false
    When the worker skills connector sync tick runs
    Then the skills connector OFF branch log appears
    And the skills connector ON branch does not run

  @happy_path @on
  Scenario: Flag ON — the skills connector pipeline runs
    Given the operator has the skills connector flag set to true
    When the worker skills connector sync tick runs
    Then the skills connector ON branch log appears
    And the skills connector OFF branch does not run
