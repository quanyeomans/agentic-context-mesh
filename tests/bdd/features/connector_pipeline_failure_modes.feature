@connector @failure_mode @resilience
Feature: ConnectorPipeline failure modes route correctly to dead-letter
  As an operator running connector syncs against unreliable sources
  I want one item's failure to be contained
  And the rest of the batch to continue processing
  So that one bad SharePoint file doesn't kill a 6,000-item sync

  Maps to test-resilience-plan.md Class D (pipeline mid-batch failures).
  The integration-layer cover lives in
  tests/integration/test_connector_pipeline_failure_injection.py;
  these BDD scenarios are the operator-readable spec.

  @happy_path
  Scenario: Clean batch — all items process, no dead-letters
    Given a connector that yields 10 change events
    When the connector pipeline runs the batch
    Then 10 items are processed successfully
    And 0 items are recorded in the dead-letter store

  @failure_mode @error
  Scenario: Fetch fails on one item — siblings still process
    Given a connector that yields 10 change events
    And the connector is scripted to fail fetch on "item-005"
    When the connector pipeline runs the batch
    Then 9 items are processed successfully
    And 1 item is recorded in the dead-letter store
    And the dead-letter row for "item-005" carries a "fetch" error prefix

  @failure_mode @error
  Scenario: Extract fails on one item — siblings still process
    Given a connector that yields 10 change events
    And an extractor that raises on the 5th call
    When the connector pipeline runs the batch
    Then 9 items are processed successfully
    And 1 item is recorded in the dead-letter store
    And the dead-letter row carries an "extract" error prefix

  @failure_mode @error
  Scenario: Writer fails mid-chunk — earlier chunks survive
    Given a connector that yields 100 change events
    And a chunk writer that raises on its 51st call
    When the connector pipeline runs the batch
    Then a RuntimeError propagates to the caller
    And the bronze_records table contains exactly 50 rows for that source

  @failure_mode @error
  Scenario: list_changes raises mid-stream — exception surfaces, not silenced
    Given a connector whose list_changes raises after yielding 3 events
    When the connector pipeline runs the batch
    Then a RuntimeError propagates to the caller
