Feature: kairix connect google-* — OAuth2 token capture for Google connectors
  As an operator setting up a Google connector for the first time
  I want to run a single command that opens the browser, captures my consent,
  and writes the canonical-named tokens to my secrets store
  So I can avoid the manual GCP console + KV copy-paste dance

  Scenario Outline: Happy path — operator completes consent and tokens are stored
    Given a Google client_secret.json downloaded to a temp path
    And a fake callback listener that will return the code "happy-code"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    When the operator runs the connect command for "<subcommand>"
    Then the command exits with status zero
    And the token store recorded one write
    And the recorded area is "<area>"
    And the success summary names the canonical secret names

    Examples:
      | subcommand        | area              |
      | google-gmail      | gmail             |
      | google-drive      | google-drive      |
      | google-calendar   | google-calendar   |

  Scenario: Consent denied — operator clicks Cancel
    Given a Google client_secret.json downloaded to a temp path
    And a fake callback listener that will simulate a denied consent
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    When the operator runs the connect command for "google-gmail"
    Then the command exits with a non-zero status
    And the error output mentions consent

  Scenario: Callback timeout — operator never completes the browser flow
    Given a Google client_secret.json downloaded to a temp path
    And a fake callback listener that will simulate a timeout
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    When the operator runs the connect command for "google-gmail"
    Then the command exits with a non-zero status
    And the error output mentions the listener wait

  Scenario: Missing client_secret.json — operator forgot to download it
    Given no Google client_secret.json on disk
    And a fake callback listener that will return the code "irrelevant"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    When the operator runs the connect command for "google-gmail"
    Then the command exits with a non-zero status
    And the error output points the operator at the GCP console

  Scenario: Already-connected — store backend rejects the write
    Given a Google client_secret.json downloaded to a temp path
    And a fake callback listener that will return the code "ok-code"
    And a fake browser that records every URL it is asked to open
    And a fake token store that raises on the next store call
    When the operator runs the connect command for "google-gmail"
    Then the command exits with a non-zero status
    And the error output mentions the store backend
