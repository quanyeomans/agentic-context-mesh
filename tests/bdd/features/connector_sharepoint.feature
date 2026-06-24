@connector @sharepoint @wave-5
Feature: SharePoint connector pulls document library binaries via Microsoft Graph
  As an operator running kairix against a Microsoft 365 tenant
  I want every change in my configured SharePoint document libraries to surface as a typed change event
  So that PDF / DOCX / PPTX / XLSX content lands in the index with the right source uri and sensitivity
  without me writing per-format glue or maintaining a parallel sync surface.

  The connector reuses the Microsoft Graph delta-token pattern the
  M365 email-headers + calendar siblings already exercise; binary
  content is fetched lazily via the per-item content endpoint and
  handed off to the kairix extractor registry for per-format
  dispatch. See docs/architecture/connector-ingestion-architecture.md
  Wave 5 + ADR-019.

  @happy_path
  Scenario: A new PDF in a configured library surfaces as a created change event
    Given a stubbed Microsoft Graph endpoint that returns one configured drive with a sample pdf envelope
    When the operator runs the sharepoint connector list_changes with no cursor
    Then one created change event is emitted
    And the change event carries an ISO-8601 modified_at timestamp
    And the change event's sensitivity tier is internal
    And the change event metadata records the source drive id

  @cursor_advance
  Scenario: The connector persists a delta link for the next tick
    Given a stubbed Microsoft Graph endpoint that returns one configured drive with a sample pdf envelope
    When the operator runs the sharepoint connector list_changes with no cursor
    Then the connector exposes a non-empty next cursor
    And the next cursor encodes a per-drive delta link map

  @site_discovery
  Scenario: A configured site auto-discovers its document libraries
    Given a stubbed Microsoft Graph site that lists two drives each with one pdf envelope
    When the operator runs the sharepoint connector list_changes with no cursor for the discovered site
    Then two created change events are emitted one per discovered drive
    And the next cursor records a delta link for each discovered drive
