@feature_flag @recommender
Feature: Operator toggles the capability recommender feature flag
  As an operator running a kairix deployment
  I want to choose whether the capability recommender is enabled
  So that I can validate the recommender before cutting it over and roll back safely if needed

  The flag gates both recommender surfaces at the adapter boundary and the
  worker's corpus build at boot. When off, the surfaces return a disabled
  envelope and the worker skips the corpus build; when on, the surfaces
  rank capabilities and the worker builds the corpus. See
  docs/architecture/feature-flag-architecture.md section 7.

  @happy_path @off
  Scenario: Flag off — the recommend surface returns a disabled response
    Given the operator has the recommender flag set to false
    When the agent asks the recommend surface which tool fits a task
    Then the recommend surface reports the recommender is disabled
    And the recommend surface returns no recommendations

  @happy_path @on
  Scenario: Flag on — the recommend surface ranks capabilities
    Given the operator has the recommender flag set to true
    And the flagged toolset includes a way to check content for conflicts
    When the agent asks the recommend surface which tool fits a task
    Then the recommend surface ranks the conflict-checking tool
    And the recommend surface reports no error

  @off
  Scenario: Flag off — the worker skips the capability corpus build at boot
    Given the operator has the recommender flag set to false
    When the worker boot corpus-build hook runs
    Then the worker does not build the capability corpus

  @on
  Scenario: Flag on — the worker builds the capability corpus at boot
    Given the operator has the recommender flag set to true
    When the worker boot corpus-build hook runs
    Then the worker builds the capability corpus
