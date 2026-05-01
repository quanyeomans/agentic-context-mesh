Feature: Search results contain no duplicate documents
  As an agent or human searching kairix
  I want each document to appear at most once in results
  So that my context budget is spent on distinct knowledge

  Scenario: No duplicates when same document indexed at different paths
    Given the mock retrieval backend is available
    When I search with duplicated paths in the index
    Then no two results share the same document path
