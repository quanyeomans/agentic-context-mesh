@feature_flag @chunker_registry_dispatch_enabled
Feature: chunker_registry_dispatch_enabled feature flag — both-branch parity (ADR-028 F54)

  The chunker_registry_dispatch_enabled flag gates whether Silver routes
  passthrough markdown through the per-type chunker registry. OFF (default)
  keeps the paragraph fallback (silver-markdown-v1); ON dispatches to the
  registered per-type chunker, stamping its per-type version.

  @happy_path @flag_off
  Scenario: flag OFF keeps the paragraph fallback chunker version
    Given an obsidian markdown document to chunk
    And the chunker_registry_dispatch_enabled flag is OFF
    When the document is processed by Silver
    Then the chunks carry the silver-markdown fallback version

  @flag_on
  Scenario: flag ON dispatches to the per-type chunker
    Given an obsidian markdown document to chunk
    And the chunker_registry_dispatch_enabled flag is ON
    When the document is processed by Silver
    Then the chunks carry a per-type chunker version
