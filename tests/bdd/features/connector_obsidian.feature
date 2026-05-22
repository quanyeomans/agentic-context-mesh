@connector @obsidian @wave-2
Feature: Obsidian connector ingests a filesystem-backed markdown vault
  As an operator running kairix against an Obsidian vault
  I want every note created, edited, or deleted in the vault to surface as a change event
  So that the index stays current without the operator running a manual rescan
  and the worker recovers cleanly from being paused mid-sync.

  The connector combines two change-detection strategies:
    1. watchdog filesystem events for live edits
    2. a full-scan reconciliation pass for events the worker missed
  See docs/architecture/connector-ingestion-architecture.md §2 + §3
  and KAIRIX-VISION-LANDSCAPE-AND-ROADMAP §4.4.

  @happy_path
  Scenario: A new note in the vault surfaces as a created change event
    Given an Obsidian vault containing three markdown notes
    When the operator runs the obsidian connector list_changes with no cursor
    Then three created change events are emitted in vault-relative order
    And every change event carries an ISO-8601 modified_at timestamp
    And every change event's item_id is a vault-relative POSIX path

  @delete
  Scenario: Deleting a note emits a deleted tombstone event on next reconcile
    Given an Obsidian vault containing three markdown notes
    And the connector has already ingested those three notes
    And the operator deletes one of the notes from the vault
    When the operator runs the obsidian connector list_changes with a stale cursor
    Then a deleted change event is emitted for the removed note's item_id
    And no created or modified events are emitted for the surviving notes

  @reconciliation
  Scenario: Reconciliation catches an event that fired while the worker was paused
    Given an Obsidian vault containing three markdown notes
    And the worker is paused so no watchdog event fires for the next edit
    And the operator edits one of the notes while the worker is paused
    When the operator restarts the worker and runs list_changes
    Then a modified change event is emitted for the edited note's item_id
    And the change event's source_link round-trips to an obsidian:// URL
