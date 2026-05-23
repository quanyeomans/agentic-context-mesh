@connector @m365_calendar @wave-5
Feature: M365 calendar connector ingests calendar events via Microsoft Graph
  As an operator running kairix against a Microsoft 365 tenant
  I want every calendar event created, edited, or cancelled in the operator's calendar to surface as a change event
  So that the index stays current with meeting context (attendees, subject, location) without manual rescans
  and the worker resumes incremental sync cleanly across restarts using a Graph delta token.

  The connector wraps Microsoft Graph's calendarView delta query and
  persists @odata.deltaLink as its cursor. Auth shares the Azure AD app
  registration with the m365_email_headers sibling connector (one app,
  two permissions: Calendar.Read + Mail.Read).
  See docs/architecture/connector-ingestion-architecture.md §10.

  @happy_path
  Scenario: First sync without a cursor surfaces a date window of events as created
    Given an operator-configured M365 calendar with two scheduled events
    When the operator runs the m365_calendar connector list_changes with no cursor
    Then two created change events are emitted in event-id order
    And every change event carries a non-empty Graph event id as item_id
    And every change event metadata payload exposes subject and attendees

  @delta
  Scenario: Subsequent sync with a delta cursor surfaces only new changes
    Given an operator-configured M365 calendar with one previously synced event
    And the Graph delta page returns one new event plus the previously seen id
    When the operator runs the m365_calendar connector list_changes with the persisted delta cursor
    Then exactly one created change event is emitted for the new event id
    And the previously seen event id surfaces as a modified change event
    And the connector exposes a persisted delta link as the next cursor

  @cancelled
  Scenario: A cancelled event surfaces as a deleted change event
    Given an operator-configured M365 calendar with one previously synced event
    And the Graph delta page returns that event marked as cancelled
    When the operator runs the m365_calendar connector list_changes with the persisted delta cursor
    Then a deleted change event is emitted for the cancelled event id
    And the connector still exposes a persisted delta link for the next cursor
