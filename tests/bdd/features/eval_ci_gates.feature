Feature: CI eval gates wire the conversation evaluation harness into PR + nightly flows
  As a platform engineer
  I want the conversation-eval gate and the LoCoMo nightly to be configured correctly
  So that regressions surface in the right loop (PR review for engagement-*, nightly trend for LoCoMo)

  Scenario: Conversation-eval gate parses as valid GitHub Actions
    Given the reflib benchmark gate workflow file exists
    When I parse the workflow as YAML
    Then it defines a job named conversation-eval-gate
    And the job calls the eval-conversation-corpora helper script
    And the job uploads the per-corpus result artifact

  Scenario: LoCoMo nightly workflow parses as valid GitHub Actions
    Given the eval-locomo-nightly workflow file exists
    When I parse the workflow as YAML
    Then it triggers on a daily schedule at 03:00 UTC
    And it runs the locomo-nightly-run helper script
    And it runs the locomo-nightly-compare helper script

  Scenario: Every engagement-* corpus has a pinned baseline file
    Given every engagement-* corpus under reference-library/conversations
    When I look for a baseline file under reference-library/conversations/expected
    Then a baseline file exists for every corpus
    And the baseline file is either a SuiteResult shape or the sentinel shape

  Scenario: Workflow files introduce no CI silencers
    Given the conversation-eval-gate and locomo-nightly workflows
    When I scan the run steps for known silencer patterns
    Then no continue-on-error: true is present without a rationale comment
    And no fail_ci_if_error: false is present without a rationale comment
