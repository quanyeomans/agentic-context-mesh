@connector @slack @wave-e
Feature: Slack connector pulls channel + DM history via the Web API
  As an operator running kairix against a Slack workspace
  I want every new channel message and DM to surface as a typed change event
  So that entity signals and timeline updates land in the index
  with the right F39 sensitivity tier per channel kind
  and DMs never leak into engagement-wide retrieval.

  Per slack.md §1, public channels map to internal-tier, private
  channels and MPIMs to client-confidential, DMs to personal. The
  connector reuses Slack's conversations.history delta surface for the
  poll path; Socket Mode handles the realtime push path with the
  reconnect state machine from slack.md §5.

  @happy_path
  Scenario: A new message in a public channel surfaces as a created change event
    Given a stubbed Slack Web API that returns one public channel with two messages
    When the operator runs the slack connector list_changes with no cursor
    Then two created change events are emitted for the public channel
    And every change event carries an ISO-8601 modified_at timestamp
    And every change event's sensitivity tier is internal

  @dm_exclusion
  Scenario: A DM message surfaces with the personal sensitivity tier
    Given a stubbed Slack Web API that returns one DM channel with one message
    When the operator runs the slack connector list_changes with no cursor
    Then one created change event is emitted for the DM
    And the change event's sensitivity tier is personal
