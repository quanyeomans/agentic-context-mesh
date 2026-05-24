@topology_v2 @applier
Feature: Wave D apply-bridge materialises parsed config into runtime topology rows
  As an operator declaring topology_v2.cc_pairs and topology_v2.collections
  in kairix.config.yaml, I want the worker to register those rows on boot
  so that CollectionRouter sees my cc_pairs and routes chunks to the
  collections I declared, instead of silently falling back to the legacy
  single-collection writer.

  The applier is idempotent — repeated boots against the same config
  produce zero new rows. Validation failures (missing cross-references)
  prevent an apply and surface the failure list to the operator.

  @happy_path
  Scenario: applying a complete topology declares cc_pairs and collections
    Given the operator has declared a topology_v2 config with one connector, one credential, one cc_pair, and one collection
    When the operator runs the apply-bridge against an empty database
    Then the apply reports one connector, one credential, one cc_pair, and two collection-shape rows as created

  Scenario: applying the same config twice is a no-op
    Given the operator has declared a topology_v2 config with one connector, one credential, one cc_pair, and one collection
    When the operator runs the apply-bridge against an empty database
    And the operator runs the apply-bridge a second time against the same database
    Then the second apply reports zero rows created and every row as unchanged
