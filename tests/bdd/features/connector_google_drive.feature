@connector @google_drive @wave-e
Feature: Google Drive connector pulls workspace files via the Drive v3 REST API
  As an operator running kairix against a Google Workspace
  I want every change in my configured Drive corpus to surface as a typed change event
  So that PDF / DOCX / PPTX / XLSX content lands in the index with the right source uri and sensitivity
  without me writing per-format glue or maintaining a parallel sync surface.

  The connector reuses the Drive v3 changes endpoint with start-page-token
  pagination. Binary content is fetched lazily via the per-file alt=media
  endpoint and handed off to the kairix extractor registry for per-format
  dispatch.

  @happy_path
  Scenario: A new file in the configured corpus surfaces as a created change event
    Given a stubbed Google Drive endpoint that returns one configured corpus with a sample pdf envelope
    When the operator runs the google drive connector list_changes with no cursor
    Then one created change event is emitted from the google drive connector
    And the google drive change event carries an ISO-8601 modified_at timestamp
    And the google drive change event's sensitivity tier is internal
    And the google drive change event metadata records the corpus id

  @cursor_advance
  Scenario: The connector persists a new start page token for the next tick
    Given a stubbed Google Drive endpoint that returns one configured corpus with a sample pdf envelope
    When the operator runs the google drive connector list_changes with no cursor
    Then the google drive connector exposes a non-empty next cursor
    And the google drive next cursor is the persisted new start page token
