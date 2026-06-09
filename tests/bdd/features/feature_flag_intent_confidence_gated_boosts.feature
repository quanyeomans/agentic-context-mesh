@feature_flag @intent_confidence_gated_boosts
Feature: Operator toggles the intent-confidence-gated-boosts feature flag
  As an operator running a kairix search pipeline
  I want to choose whether boosts gate on intent confidence
  So that ambiguous queries don't trigger unwanted boost paths

  Issue #456 — the flag changes the boost-firing rule from binary
  intent-match to intent-match + confidence-above-threshold. When OFF
  (default) every matching intent fires its boost — preserves pre-#456
  ranking byte-for-byte. When ON ambiguous queries (confidence below
  the boost's ``min_intent_confidence``) skip the boost and fall back
  to plain RRF fusion.

  @happy_path @off
  Scenario: Flag OFF — low-confidence intent still fires the boost
    Given the operator has the intent-confidence-gated-boosts flag set to false
    When the boost gate is asked about a low-confidence intent match
    Then the boost fires

  @happy_path @on
  Scenario: Flag ON — low-confidence intent skips the boost
    Given the operator has the intent-confidence-gated-boosts flag set to true
    When the boost gate is asked about a low-confidence intent match
    Then the boost is skipped

  @on
  Scenario: Flag ON — high-confidence intent still fires the boost
    Given the operator has the intent-confidence-gated-boosts flag set to true
    When the boost gate is asked about a high-confidence intent match
    Then the boost fires
