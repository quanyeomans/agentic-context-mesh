Feature: Search tuning recommendations
  As a kairix operator
  I want tuning recommendations based on benchmark results
  So that I know which parameters to adjust for weak categories

  Scenario: Weak temporal category with date files recommends temporal boost
    Given benchmark scores with temporal at 0.30 and all others at 0.70
    And the corpus has date-named files
    When the operator requests tuning recommendations
    Then a recommendation for parameter "temporal" is returned
    And the recommendation action mentions "date_path_boost"

  Scenario: Weak procedural category recommends path pattern extension
    Given benchmark scores with procedural at 0.40 and all others at 0.70
    And the corpus has procedural documents
    When the operator requests tuning recommendations
    Then a recommendation for parameter "procedural" is returned

  Scenario: All categories above floor produces no recommendations
    Given benchmark scores with all categories at 0.80
    And the corpus has date-named files
    When the operator requests tuning recommendations
    Then no recommendations are returned
