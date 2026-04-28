Feature: Auto-gold corpus analysis
  As a kairix operator
  I want the system to analyse my corpus before generating evaluation queries
  So that the evaluation suite is proportioned to my content

  Scenario: Corpus profile counts document types correctly
    Given an indexed corpus with procedural and date-named documents
    When the operator analyses the corpus
    Then the profile total_docs matches the indexed count
    And the profile procedural_count is greater than zero
    And the profile date_filename_count is greater than zero

  Scenario: Empty corpus returns zero counts
    Given an empty indexed corpus
    When the operator analyses the corpus
    Then the profile total_docs is 0

  Scenario: Generated queries are proportioned by corpus type
    Given an indexed corpus with procedural and date-named documents
    When the operator generates template queries with count 20
    Then at least one query has category "procedural"
    And at least one query has category "recall"
    And the total query count is 20
