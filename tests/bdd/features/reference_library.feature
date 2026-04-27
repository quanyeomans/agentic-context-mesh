Feature: Reference library search
  As a kairix user with the reference library indexed
  I want to search across knowledge domains
  So that I find relevant documents from the curated collection

  Scenario: Search finds engineering documents
    Given the reference library fixture is indexed
    When I search for "architecture decision record"
    Then at least 1 result is returned
    And the top result is from the engineering collection

  Scenario: Search finds philosophy documents
    Given the reference library fixture is indexed
    When I search for "Stoic philosophy"
    Then at least 1 result is returned

  Scenario: Search results have no frontmatter in snippets
    Given the reference library fixture is indexed
    When I search for "twelve factor app"
    Then no result snippet starts with "---"

  Scenario: BM25 and vector search both contribute
    Given the reference library fixture is indexed
    When I search for "chain of thought prompting"
    Then results include BM25 matches
