@connector @github @wave-e
Feature: GitHub connector polls per-repo for code, issues, and pull requests
  As an operator running kairix against a GitHub organisation
  I want every commit, issue, and pull request update to surface as a typed change event
  So that knowledge-store retrieval stays current without an operator-triggered re-sync
  and a missing credential or signature-failed webhook surfaces an actionable error.

  The connector reads from the GitHub REST + GraphQL APIs. Each
  configured repository is its own Container with its own per-repo
  cursor (commit SHA for code, since= timestamp for issues / PRs).
  Inbound webhook deliveries are HMAC-256 verified against the
  operator-configured secret; signature failures are rejected
  outright. The installation token rotates at 50 percent of its TTL
  under a per-cc_pair lock so a long backfill never outruns the
  token. See docs/architecture/connector-scope-topology/connector-design-specs/github.md
  and docs/architecture/feature-flag-architecture.md section 7.

  @happy_path
  Scenario: A new commit in a configured repository surfaces as a modified change event
    Given a stubbed GitHub API endpoint that lists one repository with one new commit since the cursor
    When the operator runs the github connector list_changes with no cursor
    Then one modified change event is emitted
    And the change event item_id encodes the repository full name and commit sha
    And the change event sensitivity tier matches the repository visibility tier
    And the change event metadata records the source repository

  @cursor_isolation
  Scenario: Each repository advances its cursor independently
    Given a stubbed GitHub API endpoint that lists two repositories each with one new commit
    When the operator runs the github connector list_changes with no cursor
    Then two modified change events are emitted one per repository
    And the persisted cursor records a distinct value for each repository

  @webhook_signature
  Scenario: A webhook delivery with a bad HMAC signature is rejected
    Given a webhook envelope whose X-Hub-Signature-256 header does not match the body HMAC
    When the operator hands the envelope to the github connector handle_event
    Then a webhook signature error is raised
    And the operator sees an actionable error naming the failing field

  @missing_credentials
  Scenario: A missing credential surfaces an actionable error
    Given neither the github personal access token nor the App triple is configured
    When the operator constructs the github connector via the make_connector entry point
    Then the operator sees an actionable error naming the required secrets

  @repos_allowlist
  Scenario: A repos_allowlist restricts the drain to the operator-named repositories
    Given a stubbed GitHub API endpoint that lists three repositories
    And an operator-configured repos_allowlist naming exactly two of those repositories
    When the operator runs the github connector list_changes with no cursor
    Then change events are emitted only for the allowlisted repositories
    And no change event references the excluded repository

  @repos_allowlist_unset
  Scenario: An unset repos_allowlist drains every installation-accessible repository
    Given a stubbed GitHub API endpoint that lists three repositories
    And no repos_allowlist is configured
    When the operator runs the github connector list_changes with no cursor
    Then change events are emitted for every repository in the installation
