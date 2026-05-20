Feature: Run the eval suite benchmark against a conversation corpus
  As an operator validating retrieval quality before a release
  I want to run kairix eval against a suite directory
  And compare the result against a pinned baseline
  So that regressions are caught before they reach production

  Scenario: Happy path — operator sees per-category pass rates
    Given a suite directory with 3 single-hop questions and 1 multi-hop question
    And a configured backend that scores every question correctly
    When the operator runs kairix eval against the suite directory
    Then the suite passes all 4 questions
    And the report contains a single-hop category line
    And the report contains a multi-hop category line

  Scenario: Regression gate detects degraded mean score
    Given a suite directory with 4 questions
    And a pinned baseline whose mean score is 0.9
    And a configured backend that scores every question at 0.2
    When the operator runs kairix eval with regression-against the baseline directory
    Then the eval exits with a regression failure
    And the report names the suite that regressed

  Scenario: Regression gate passes when run beats the baseline
    Given a suite directory with 2 questions
    And a pinned baseline whose mean score is 0.5
    And a configured backend that scores every question at 0.95
    When the operator runs kairix eval with regression-against the baseline directory
    Then the eval exits with success

  Scenario: Missing ground truth file surfaces an actionable error
    Given a suite directory that is missing the ground truth queries file
    When the operator runs kairix eval against the suite directory
    Then the eval exits with an actionable error message about ground-truth-queries.json
