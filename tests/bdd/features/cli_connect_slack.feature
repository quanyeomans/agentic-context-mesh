Feature: kairix connect slack — OAuth v2 bot-token capture for Slack workspaces
  As an operator setting up a Slack workspace connector
  I want to run a single command that opens the browser, captures my workspace consent,
  and writes the canonical-named bot-token to my secrets store under a per-workspace slot
  So I can avoid the manual Slack-app-install + KV copy-paste dance

  Scenario: Happy path — operator completes workspace install and tokens are stored
    Given a Slack OAuth client_id and client_secret
    And a fake callback listener that will return the code "slack-happy-code"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every Slack store call
    When the operator runs the slack connect command for workspace "alpha"
    Then the slack connect command exits with status zero
    And the slack token store recorded one write
    And the slack recorded area is "slack"
    And the slack recorded instance is "alpha"
    And the slack success summary names the canonical bot token

  Scenario: Consent denied — operator clicks Cancel on the workspace install screen
    Given a Slack OAuth client_id and client_secret
    And a fake callback listener that will simulate a denied Slack consent
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every Slack store call
    When the operator runs the slack connect command for workspace "alpha"
    Then the slack connect command exits with a non-zero status
    And the slack error output mentions consent

  Scenario: Port collision — listener factory raises OSError
    Given a Slack OAuth client_id and client_secret
    And a listener factory that raises a port-in-use OSError
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every Slack store call
    When the operator runs the slack connect command for workspace "alpha"
    Then the slack connect command exits with a non-zero status
    And the slack error output mentions the port

  Scenario: Missing client credentials — operator forgot to supply client_id
    Given no Slack client credentials supplied
    When the operator runs the slack connect command missing the client_id
    Then the slack connect command exits with a non-zero status
    And the slack argparse error mentions the missing client_id argument

  Scenario: Callback timeout — operator never completes the browser flow
    Given a Slack OAuth client_id and client_secret
    And a fake callback listener that will simulate a Slack timeout
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every Slack store call
    When the operator runs the slack connect command for workspace "alpha"
    Then the slack connect command exits with a non-zero status
    And the slack error output mentions the callback wait

  Scenario: Already-connected — store backend rejects the write
    Given a Slack OAuth client_id and client_secret
    And a fake callback listener that will return the code "slack-ok-code"
    And a fake browser that records every URL it is asked to open
    And a fake token store that raises on the next Slack store call
    When the operator runs the slack connect command for workspace "alpha"
    Then the slack connect command exits with a non-zero status
    And the slack error output mentions the store backend
