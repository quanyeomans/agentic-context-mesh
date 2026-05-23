@connector @m365_email_headers @wave-5
Feature: M365 email-headers connector pulls envelope metadata via Microsoft Graph
  As an operator running kairix against a Microsoft 365 mailbox
  I want every new email's From / To / CC / Subject / Date metadata to surface as a change event
  So that entity signals and timeline updates land in the index
  without the body content ever being fetched from Microsoft Graph.

  Per ADR-004 (Email — Headers Only), the connector MUST never fetch
  message bodies. Header-only retrieval is enforced at the Graph
  query layer via a $select projection that excludes every body field.
  See docs/architecture/connector-ingestion-architecture.md Wave 5.

  @happy_path
  Scenario: A new message in the mailbox surfaces as a created change event
    Given a stubbed Microsoft Graph endpoint that returns three header-only messages
    When the operator runs the m365_email_headers connector list_changes with no cursor
    Then three created change events are emitted
    And every change event carries an ISO-8601 modified_at timestamp
    And every change event's sensitivity tier is personal

  @no_body_content
  Scenario: The Graph delta query NEVER asks for body content
    Given a stubbed Microsoft Graph endpoint that records every requested URL
    When the operator runs the m365_email_headers connector list_changes with no cursor
    Then the recorded Graph URL contains a $select projection
    And the recorded Graph URL projection does not contain body
    And the recorded Graph URL projection does not contain bodyPreview
    And the recorded Graph URL projection does not contain uniqueBody
