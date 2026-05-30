@connector @gmail @wave-5
Feature: Gmail connector pulls message body + envelope via the Gmail REST API
  As an operator running kairix against a Google Workspace mailbox
  I want every new message's body and envelope metadata to surface as a change event
  So that retrieval can find the message by subject, sender, or body content
  and entity signals + timeline updates land in the index.

  Per the Onyx Gmail design pattern, a single connector extracts BOTH the
  body and the headers. No separate body / headers flags — the message
  is one document with the envelope on SourceMetadata and the body
  bytes on RawArtefact. See kairix/connectors/gmail/README.md and
  docs/architecture/connector-ingestion-architecture.md Wave 5.

  @happy_path
  Scenario: A new message in the mailbox surfaces as a created change event
    Given a stubbed Gmail History API that returns two new messages since the cursor
    When the operator runs the gmail connector list_changes with a warm cursor
    Then two created change events are emitted
    And every change event carries the gmail mailbox sensitivity tier
    And the first change event item_id round-trips to a gmail.google.com source link
