@connector @per_tick_budget @f66 @adr_020
Feature: Connector pipeline per-tick budget + disk-watermark gating
  As an operator running incremental syncs on a shared host
  I want each tick to do bounded work and yield when disk is low
  So that a large first-sync converges over many ticks instead of one
  multi-hour run, and a near-full disk gets a clean watermark skip
  instead of a partial-disk-write crash (ADR-020).

  @happy_path
  Scenario: Per-tick budget caps work and converges over three ticks
    Given a connector "budget-source" with 1500 change events queued
    And the connector declares a per_tick_max_items of 500
    When the operator runs three consecutive pipeline ticks for "budget-source"
    Then tick 1 processes exactly 500 items and yields with budget_yielded True
    And tick 2 processes exactly 500 items and yields with budget_yielded True
    And tick 3 processes exactly 500 items and the cursor advances each tick
    And the persisted cursor row has advanced three times across the three ticks

  Scenario: Watermark gate skips the tick when free disk falls below the threshold
    Given a connector "watermark-source" with five change events queued
    And the connector declares a disk_watermark_min_free_bytes of five gibibytes
    And the disk_free_resolver reports only one gibibyte free
    When the operator runs one pipeline tick for "watermark-source"
    Then zero items are processed
    And the BatchResult reports skipped_low_disk True
    And the persisted cursor row is unchanged
