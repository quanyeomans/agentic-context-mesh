Feature: Worker preflight — persistence integrity audit at boot and on demand
  As a kairix operator
  I want a structured preflight that surfaces gaps between documents, FTS, and vectors
  So that a degraded VM is caught before BM25 silently falls back to vector-only

  Scenario: Preflight passes on a clean database
    Given a fresh kairix database with no documents
    When the operator runs worker preflight
    Then the preflight exit code is 0
    And the preflight output contains "PASSED"

  Scenario: Preflight surfaces documents-without-fts gap
    Given a kairix database with active documents missing FTS rows
    When the operator runs worker preflight
    Then the preflight exit code is 1
    And the preflight output contains "documents-without-fts"
    And the preflight remediation mentions rebuild-fts

  Scenario: Auto-heal rebuilds the FTS index when documents-without-fts is present
    Given a kairix database with active documents missing FTS rows
    When the operator runs worker preflight with auto-heal
    Then the preflight exit code is 0
    And the FTS index now has rows for every active document
