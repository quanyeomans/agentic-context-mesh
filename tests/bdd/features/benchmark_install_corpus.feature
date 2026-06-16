Feature: Install the reference-library corpus on demand
  As an operator who installed kairix with pip
  I want to fetch the reference-library corpus that is not bundled in the wheel
  So that I can run the reflib benchmark suite after a plain pip install

  Scenario: Operator installs the corpus and the reflib suite finds it
    Given a pip-installed kairix with no reference corpus
    When the operator runs kairix benchmark install-corpus
    Then the corpus is fetched and verified
    And the install command reports success
    And the reflib suite can resolve the installed corpus

  Scenario: A corrupt download fails closed
    Given a pip-installed kairix with no reference corpus
    When the operator runs kairix benchmark install-corpus with a corrupt download
    Then the install command fails closed
    And no corpus is left installed
