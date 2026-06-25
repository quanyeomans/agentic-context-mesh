@feature_flag @re_chunk_sweep_enabled
Feature: re_chunk_sweep_enabled feature flag — both-branch parity (ADR-028 F54)

  The re_chunk_sweep_enabled flag gates whether the worker runs the bounded
  re-chunk sweep maintenance tick. OFF (default) is a complete no-op; ON (with
  chunker_registry_dispatch_enabled also ON) runs the sweep, re-chunking stale
  documents from their persisted source markdown.

  @happy_path @flag_off
  Scenario: flag OFF skips the re-chunk sweep
    Given a worker re-chunk sweep maintenance tick
    And the re_chunk_sweep_enabled flag is OFF
    When the maintenance tick fires
    Then the re-chunk sweep does not run

  @flag_on
  Scenario: flag ON runs the re-chunk sweep
    Given a worker re-chunk sweep maintenance tick
    And the re_chunk_sweep_enabled flag is ON
    When the maintenance tick fires
    Then the re-chunk sweep runs
