@source_tier @issue_432
Feature: Operator tier mapping shapes search ranking
  As an operator with both canonical and reference content in scope
  I want canonical-tier content to outrank reference-tier content
  So that vault canon beats external fragments on tie

  Issue #432 — the SourceTierBoost multiplies each result's
  ``boosted_score`` by a per-collection tier multiplier
  (canonical x3.0 → vault_active x1.0 → reference x0.6 → archived x0.2
  by default). The tier mapping flows from per-collection
  ``tier:`` declarations in the operator's ``kairix.config.yaml``.

  @happy_path @on
  Scenario: Canonical collection outranks reference collection on tie
    Given the operator has source-tier boost enabled
    And a chunk in collection 'vault-canon' at tier 'canonical'
    And a chunk in collection 'reference-library' at tier 'reference'
    When the operator searches across both collections
    Then the canonical-tier chunk ranks above the reference-tier chunk

  @off
  Scenario: With the boost disabled, tier mapping has no effect
    Given the operator has source-tier boost disabled
    And a chunk in collection 'vault-canon' at tier 'canonical'
    And a chunk in collection 'reference-library' at tier 'reference'
    When the operator searches across both collections
    Then neither chunk gains a tier multiplier
