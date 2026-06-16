Feature: Web setup wizard — browser-reachable remote access (#500)
  As an operator running kairix on stock Docker
  I want to open the setup wizard in my browser from a tunnel or another machine
  So that I can finish onboarding even though my browser cannot send custom headers

  # On stock Docker every published-port connection arrives as the bridge
  # gateway IP — never loopback — and browsers cannot send the
  # X-Kairix-Operator-Token header. The tokened URL grants a signed cookie
  # the wizard accepts (the Jupyter pattern), so a browser reaches the
  # wizard while a browser without the tokened URL stays blocked.

  Scenario: A tunnelled browser reaches the wizard with the tokened URL
    Given the setup wizard is reachable only with an operator token
    When a browser on the docker bridge opens the tokened wizard URL
    Then the wizard grants a session cookie and sends the browser to the start
    When the browser opens the provider step carrying that cookie
    Then the provider step is shown

  Scenario: A browser without the tokened URL is blocked
    Given the setup wizard is reachable only with an operator token
    When a browser on the docker bridge opens the wizard without the tokened URL
    Then access is refused with guidance to open the tokened URL

  Scenario: The host-shell operator reaches the wizard with no token
    Given the setup wizard is reachable only with an operator token
    When the operator opens the wizard from the host shell on loopback
    Then the provider step is shown
