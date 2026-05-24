Feature: KFEAT-018 paydown doc snapshot stays current with the most recent release
  As an operator preparing to cut a release
  I want the grandfathering-paydown doc to have a fresh State snapshot
  So that the paydown plan I'm shipping reflects the baselines I'm shipping

  Scenario: Fresh snapshot within 7 days of the most recent release tag passes
    Given a paydown doc snapshot dated within 7 days of the most recent release tag
    When I run the paydown-doc currency check
    Then the check exits 0
    And the output reports the snapshot is within the freshness window

  Scenario: Stale snapshot more than 7 days old without an extension comment fails
    Given a paydown doc snapshot dated 50 days before the most recent release tag
    And no expected-out-of-date-until comment is present
    When I run the paydown-doc currency check
    Then the check exits 1
    And the output names the snapshot date and the release tag date
    And the output carries the fix / next / run action markers

  Scenario: Stale snapshot with a forward-dated extension comment passes
    Given a paydown doc snapshot dated 50 days before the most recent release tag
    And an expected-out-of-date-until comment dated in the future is present
    When I run the paydown-doc currency check
    Then the check exits 0
    And the output reports the extension comment was honoured
