Feature: kairix connect github-app — GitHub App install + JWT token capture
  As an operator setting up the kairix GitHub connector in App mode
  I want a single command that opens the App install URL, captures the installation_id,
  signs a JWT with my App's private key, and writes the canonical-named secrets
  So I can avoid the manual GitHub-settings-then-KV copy-paste dance

  Scenario: Happy path — operator completes install and tokens are stored
    Given a GitHub App private key on disk
    And a fake callback listener that will return the install id "11111"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that returns the installation token "fake-installation-token"
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with status zero
    And the github-app token store recorded one write
    And the github-app recorded area is "github"
    And the github-app success summary names the canonical secret names
    And the github-app token metadata carries the installation id

  Scenario: Private key unreadable — operator pointed at a missing PEM
    Given no GitHub App private key on disk
    And a fake callback listener that will return the install id "ignored"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that returns the installation token "ignored"
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with a non-zero status
    And the github-app error output points the operator at the GitHub App settings

  Scenario: Install callback timeout — operator never completes the browser flow
    Given a GitHub App private key on disk
    And a fake callback listener that will simulate a timeout
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that returns the installation token "ignored"
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with a non-zero status
    And the github-app error output mentions the install callback wait

  Scenario: Malformed private key — file exists but is not a PEM
    Given a malformed GitHub App private key on disk
    And a fake callback listener that will return the install id "ignored"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that returns the installation token "ignored"
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with a non-zero status
    And the github-app error output mentions the private key format

  Scenario: JWT signing failure — pyjwt rejects the key
    Given a GitHub App private key on disk
    And a fake callback listener that will return the install id "22222"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that raises a signing failure
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with a non-zero status
    And the github-app error output mentions JWT signing

  Scenario: GitHub rejects installation-token exchange — bad installation id
    Given a GitHub App private key on disk
    And a fake callback listener that will return the install id "33333"
    And a fake browser that records every URL it is asked to open
    And a fake token store that records every store call
    And a fake JWT exchanger that raises a token-exchange rejection
    When the operator runs the github-app connect command with --app-id "42"
    Then the github-app command exits with a non-zero status
    And the github-app error output mentions the installation-token exchange
