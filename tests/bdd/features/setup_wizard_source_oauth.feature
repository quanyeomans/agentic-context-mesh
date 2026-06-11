Feature: Web setup wizard — connect a chat workspace or code host as a source
  As an operator setting up kairix in the browser
  I want to connect sources like a chat workspace by signing in with the account I already use
  So that kairix indexes the channels and repositories I pick — and nothing else

  Scenario: An operator connects a chat workspace and picks channels
    Given the setup wizard is enabled with a wizard backend ready to connect sources
    When the operator opens the source step
    Then the source step offers a folder and the connectable sources
    When the operator opens the connect form for the chat workspace
    Then the connect form shows the exact address to register with the provider
    When the operator submits the workspace connection details
    Then the wizard waits for the provider sign-in to finish
    When the provider sends the browser back with an approval
    And the sign-in finishes
    Then the picker lists the channels the workspace offers
    When the operator picks two channels and saves
    Then the wizard states what will be fetched before anything is downloaded

  Scenario: A cancelled consent screen explains what happened
    Given the setup wizard is enabled with a wizard backend whose source sign-in was cancelled
    When the operator opens the wait screen for the chat workspace
    And the wizard checks the sign-in progress
    Then the wizard explains the sign-in was cancelled and how to retry

  Scenario: A stray sign-in response is turned away
    Given the setup wizard is enabled with a wizard backend with no sign-in in progress
    When a sign-in response arrives without a connection waiting
    Then the wizard turns it away and explains how to start a connection
