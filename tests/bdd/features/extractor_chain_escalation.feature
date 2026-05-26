@extractor @wave_3 @escalation
Feature: EscalatingExtractor chains extractors via quality_ok
  As an operator with mixed-quality source documents
  I want to declare a chain of extractors per connector
  And have the orchestrator fall through to the next tier when quality_ok=False
  So that image-only PDFs route to OCR without me coding the dispatch
  And so that pathological files don't kill the whole sync

  The escalation chain wraps an ordered list of Extractor instances
  (markitdown → pdf_fallback → ocr) and walks them via the
  Extractor Protocol's quality_ok method. Plugins know nothing about
  escalation — the framework owns the chain. See
  ``docs/architecture/connector-ingestion-architecture.md`` § 4 for
  the spec.

  @happy_path
  Scenario: First tier succeeds — chain short-circuits without invoking later tiers
    Given an escalating chain wrapping "markitdown" then "ocr"
    And a payload that markitdown will recover with quality_ok true
    When the operator invokes extract on the chain
    Then the chain returns the markitdown output
    And the escalation trace shows markitdown won
    And the escalation trace records exactly one step
    And the escalation trace is not marked exhausted

  @happy_path
  Scenario: First tier fails quality_ok — chain falls through to second tier
    Given an escalating chain wrapping "markitdown" then "ocr"
    And a payload that markitdown will recover with quality_ok false but ocr will recover with quality_ok true
    When the operator invokes extract on the chain
    Then the chain returns the ocr output
    And the escalation trace shows ocr won
    And the escalation trace records exactly two steps
    And the escalation trace is not marked exhausted

  @error
  Scenario: All tiers return quality_ok=False — chain exhausted with longest attempt
    Given an escalating chain wrapping "markitdown" then "ocr"
    And every tier in the chain will return quality_ok false
    When the operator invokes extract on the chain
    Then the chain returns the longest-markdown attempt
    And the escalation trace is marked exhausted

  @error
  Scenario: One tier raises — chain continues to the next tier
    Given an escalating chain whose first tier raises during extract
    And a second tier that recovers cleanly
    When the operator invokes extract on the chain
    Then the chain returns the second tier's output
    And the escalation trace records the first tier's exception class

  @config
  Scenario: Operator opts in via extractor_chain config field
    Given a connector config with extractor_chain set to "passthrough,passthrough"
    When the operator builds the extractor from the config entry
    Then the result is an EscalatingExtractor wrapping the named tiers

  @config
  Scenario: Operator config with extractor single-name still works (backward compatible)
    Given a connector config with extractor set to "passthrough"
    When the operator builds the extractor from the config entry
    Then the result is a single passthrough extractor
    And the result is not an EscalatingExtractor

  @config
  Scenario: Operator typo on extractor_chain shape fails fast with fix pointer
    Given a connector config with extractor_chain set to a single string instead of a list
    When the operator builds the extractor from the config entry
    Then the build raises ValueError mentioning "extractor_chain must be a list"
