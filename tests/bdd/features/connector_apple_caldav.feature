@connector @apple_caldav @wave-5
Feature: Apple CalDAV connector ingests iCloud calendar events
  As an operator running kairix against an Apple iCloud account
  I want every calendar event created, edited, or cancelled in any iCloud calendar to surface as a change event
  So that the index stays current with meeting context (attendees, summary, location, recurrence) without manual rescans
  and the worker resumes incremental sync cleanly across restarts using the CalDAV sync token.

  The connector wraps the CalDAV <sync-collection> REPORT (RFC 6578)
  and persists the per-calendar sync token as its cursor. Auth is HTTP
  Basic with an Apple-issued app-specific password — the operator's
  primary iCloud password is never accepted. See
  kairix/connectors/apple_caldav/README.md for the operator setup.

  @happy_path
  Scenario: First sync without a cursor surfaces every event as created
    Given an operator-configured iCloud account with two scheduled events
    When the operator runs the apple_caldav connector list_changes with no cursor
    Then two created change events are emitted in event-id order from apple_caldav
    And every apple_caldav change event carries a non-empty event id as item_id
    And every apple_caldav change event metadata payload exposes summary and attendees

  @delta
  Scenario: Subsequent sync with a sync token surfaces only new changes
    Given an operator-configured iCloud account with one previously synced event
    And the CalDAV sync REPORT returns one new event plus the previously seen id
    When the operator runs the apple_caldav connector list_changes with the persisted sync token
    Then exactly one created change event is emitted for the new event id from apple_caldav
    And the previously seen event id surfaces as a modified change event from apple_caldav
    And the apple_caldav connector exposes a persisted sync token as the next cursor

  @cancelled
  Scenario: A cancelled event surfaces as a deleted change event
    Given an operator-configured iCloud account with one previously synced event
    And the CalDAV sync REPORT returns that event marked as cancelled
    When the operator runs the apple_caldav connector list_changes with the persisted sync token
    Then a deleted change event is emitted for the cancelled event id from apple_caldav
    And the apple_caldav connector still exposes a persisted sync token for the next cursor
