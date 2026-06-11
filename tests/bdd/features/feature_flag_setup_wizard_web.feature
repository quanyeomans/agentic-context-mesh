Feature: setup_wizard_web feature flag — the wizard mounts only when enabled
  As a kairix operator
  I want the web setup wizard to stay absent until I deliberately enable it
  So that upgrading kairix changes nothing about my server until I choose the cutover

  Scenario: Flag ON — the wizard is served
    Given the setup wizard flag is ON
    When the operator opens the setup wizard
    Then the welcome screen invites them to get started

  Scenario: Flag OFF — the wizard is absent
    Given the setup wizard flag is OFF
    When the operator requests the setup wizard
    Then the server reports there is no such page
