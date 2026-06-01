@feature_flag @topology_v2_default_in_scope
Feature: topology_v2_default_in_scope feature flag gates the default-in-scope filter
  As an operator running kairix against the v2 collection topology
  I want default search to return ONLY the in-default subset of an agent's
  scope when the flag is ON
  And the existing read-everything behaviour to remain when the flag is OFF
  So that I can stage the cutover from "every read-eligible collection
  surfaces by default" to "only operator-curated in-default collections
  surface by default" without an operator-visible jump.

  GH #373 — the flag introduces the ``default_in_scope`` filter wired into
  the ScopeProfileResolver. The flag defaults OFF so existing operators see
  bit-for-bit pre-#373 behaviour; flipping ON activates the filter and the
  default-search superset shrinks to the in-default subset.

  @happy_path @off
  Scenario: Flag OFF returns every read-eligible scope entry (back-compat)
    Given a scope profile with 7 in-default and 1 opt-in scope entries for agent "shape"
    And the operator has the topology-v2-default-in-scope flag set to false
    When the operator resolves the default collection list for agent "shape"
    Then every scope-eligible collection name is returned, including the opt-in collection

  @happy_path @on
  Scenario: Flag ON filters default search to the in-default subset only
    Given a scope profile with 7 in-default and 1 opt-in scope entries for agent "shape"
    And the operator has the topology-v2-default-in-scope flag set to true
    When the operator resolves the default collection list for agent "shape"
    Then only the 7 in-default collection names are returned
    And the opt-in collection name is not in the result
