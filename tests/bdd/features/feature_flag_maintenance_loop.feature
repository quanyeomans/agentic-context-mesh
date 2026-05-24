@feature_flag @maintenance_loop
Feature: maintenance_loop feature flag gates KFEAT-021 Phase 1 background cleanup
  As an operator landing the background maintenance loop (per KFEAT-021)
  I want the periodic MaintenanceScheduler.tick to run inside the worker
  loop ONLY when the flag is on
  So that the cutover is reversible — flipping the flag OFF restores
  bit-for-bit pre-KFEAT-021 behaviour (no orphan-vector pruning, no
  usearch rebuild, no soft-delete table writes).

  Phase 1 is the proactive replacement for the reactive preflight check.
  When OFF (default-safe), the worker loop never calls the scheduler.
  When ON, every interval (default 24 h via KAIRIX_MAINTENANCE_INTERVAL_S)
  the scheduler prunes orphan content_vectors into content_vectors_pruned
  with a 7-day retention window before hard-deleting.

  @happy_path @off
  Scenario: flag default-off — worker loop never invokes the scheduler
    Given the operator has the maintenance-loop flag set to false
    When the worker loop reaches its maintenance-tick dispatch slot
    Then no MaintenanceScheduler.tick fires
    And the content_vectors_pruned table stays empty

  @on
  Scenario: flag effective-true — worker loop fires the scheduler
    Given the operator has the maintenance-loop flag set to true
    And the database has at least one orphan content_vectors row
    When the worker loop reaches its maintenance-tick dispatch slot
    Then a MaintenanceScheduler.tick fires
    And the orphan row is moved into content_vectors_pruned
    And the structured maintenance_tick_completed log event is emitted
