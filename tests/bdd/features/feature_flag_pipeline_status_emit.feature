@feature_flag @pipeline_status_emit
Feature: pipeline_status_emit feature flag — both-branch parity (ADR-025 F54)

  The pipeline_status_emit flag gates whether status_emit calls reach the
  pipeline_item_status table. OFF (default) makes emit_for a no-op
  context manager; ON routes writes to the timeline.

  @happy_path @flag_off
  Scenario: flag OFF leaves pipeline_item_status untouched
    Given a fresh kairix database
    And the pipeline_status_emit flag is OFF
    When the pipeline emits one extract status for an item
    Then the pipeline_item_status table is empty

  @flag_on
  Scenario: flag ON appends the emit row
    Given a fresh kairix database
    And the pipeline_status_emit flag is ON
    When the pipeline emits one extract status for an item
    Then the pipeline_item_status table contains exactly one row
    And the row records the EXTRACT_OK status with severity ok
