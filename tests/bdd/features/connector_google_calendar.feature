@connector @google_calendar @wave-e
Feature: Google Calendar connector ingests events via the Calendar API v3
  As an operator running kairix against a Google Workspace tenant
  I want every calendar event created, edited, or cancelled in the operator's calendar to surface as a change event
  So that the index stays current with meeting context (attendees, summary, location, recurrence) without manual rescans
  and the worker resumes incremental sync cleanly across restarts using a Google nextSyncToken.

  The connector wraps the Calendar API v3 events.list endpoint and
  persists the returned nextSyncToken as its cursor. Cancelled events
  are skipped per ADR-028; recurring master events surface once with
  the RRULE captured in metadata (no per-occurrence expansion).
  See kairix/connectors/google_calendar/README.md.

  @happy_path
  Scenario: First sync without a cursor surfaces a window of events as created
    Given an operator-configured Google Calendar with two scheduled events
    When the operator runs the google_calendar connector list_changes with no cursor
    Then two google_calendar created change events are emitted in event-id order
    And every google_calendar change event carries a non-empty Google event id as item_id
    And every google_calendar change event metadata payload exposes summary and attendees

  @delta
  Scenario: Subsequent sync with a sync token surfaces only new changes
    Given an operator-configured Google Calendar with one previously synced event
    And the events list page returns one new event plus the previously seen id
    When the operator runs the google_calendar connector list_changes with the persisted sync token
    Then exactly one google_calendar created change event is emitted for the new event id
    And the previously seen google_calendar event id surfaces as a modified change event
    And the google_calendar connector exposes a persisted sync token as the next cursor

  @cancelled
  Scenario: A cancelled event does not surface as a change event
    Given an operator-configured Google Calendar with one previously synced event
    And the events list page returns that event marked as cancelled
    When the operator runs the google_calendar connector list_changes with the persisted sync token
    Then no google_calendar change event is emitted for the cancelled event id
    And the google_calendar connector still exposes a persisted sync token for the next cursor

  @recurrence
  Scenario: A recurring master event surfaces once with the RRULE captured in metadata
    Given an operator-configured Google Calendar with one recurring master event
    When the operator runs the google_calendar connector list_changes with no cursor
    Then exactly one google_calendar created change event is emitted for the recurring master id
    And the google_calendar connector source metadata for the recurring master id carries the RRULE
