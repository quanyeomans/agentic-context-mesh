Feature: Web setup wizard — guided first-run onboarding in the browser
  As an operator installing kairix for the first time
  I want a guided web wizard that walks me from an empty install to a connected agent
  So that I reach my first successful search without reading any docs

  Scenario: Operator completes the full setup journey
    Given the setup wizard is enabled with a ready wizard backend
    When the operator opens the setup wizard
    Then the welcome screen invites them to get started
    When the operator continues to the provider step
    Then the provider step lists the available AI providers
    When the operator validates their provider key
    Then the key is accepted and the available models are shown
    When the operator saves the provider key
    Then the wizard advances to the folder step
    When the operator scans the folder "~/Documents"
    Then the scan reports the files found and the estimated cost
    When the operator starts indexing
    And the indexing run completes
    Then the wizard advances to the capability tour
    When the operator searches for "project kickoff"
    Then the search shows results from their documents
    When the operator opens the connect-agent step
    Then the connect-agent step shows the address agents use to connect
    When the operator verifies the agent connection
    Then the wizard confirms the connection with the available tool count
    When the operator opens the finish screen
    Then the finish screen celebrates the indexed knowledge

  Scenario: A rejected provider key shows guidance instead of jargon
    Given the setup wizard is enabled with a wizard backend that rejects provider keys
    When the operator validates their provider key
    Then the key is rejected with guidance to fix and retry

  Scenario: An Azure operator validates their key with a deployment name
    Given the setup wizard is enabled with a ready wizard backend
    When the operator opens the key step for an Azure provider
    Then the key step offers a deployment name field
    When the operator validates their provider key with the deployment name "embed-deploy"
    Then the key is accepted and the available models are shown

  Scenario: A read-only config file does not strand the operator
    Given the setup wizard is enabled with a wizard backend whose config file cannot be written
    When the operator saves the provider key
    Then the wizard explains the config file is read-only and how to make saves stick

  Scenario: The capability tour shows the five moves
    Given the setup wizard is enabled with a ready wizard backend
    When the operator opens the capability tour
    Then the tour offers five sample runs, each naming the tool agents use for it
    When the operator searches for "project kickoff"
    Then the search shows results from their documents
    When the operator runs the context pack sample
    Then the context pack sample shows a summary built from their documents
    When the operator runs the remember sample
    Then the remember sample shows the memory was saved and found again by search
    When the operator runs the briefing sample
    Then the briefing sample shows a briefing built from recent activity
    When the operator runs the timeline sample
    Then the timeline sample shows recent activity with dates
    When the operator opens the finish screen
    Then the finish screen names the tool agents use for each capability

  Scenario: A fresh knowledge store explains an empty brief honestly
    Given the setup wizard is enabled with a wizard backend over a freshly indexed knowledge store
    When the operator runs the briefing sample
    Then the briefing sample explains the brief fills in as the team works
