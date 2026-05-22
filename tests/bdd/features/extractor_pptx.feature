@extractor @wave_4 @pptx
Feature: Pptx extractor preserves per-slide structure and speaker notes
  As an operator ingesting slide decks
  I want the pptx extractor to claim the Office Open XML presentation mime
  And to emit one Page per slide with the slide title carried through
  And to lift speaker notes into the document markdown as block quotes
  So that retrieval can cite back to a specific slide
  And queries can surface what the presenter actually said off-slide.

  This feature pins the OF-1 (Wave 4) shape — pptx is the slide-aware
  extractor that sits behind markitdown on the escalation chain. See
  ``docs/architecture/connector-ingestion-architecture.md`` §10 (Wave 4
  OF-1) and KFEAT-012 Phase 2 §PowerPoint.

  @happy_path
  Scenario: Operator extracts a three-slide deck via the pptx plugin
    Given the pptx extractor is registered under the name "pptx"
    And the operator has a scripted three slide presentation
    When the operator asks the pptx extractor whether it can extract the office open xml presentation mime
    Then the pptx extractor claims the mime type
    When the operator invokes the pptx extractor's extract method on the bytes
    Then the pptx document carries one page per slide
    And the pptx document carries each slide title in the page text
    And the pptx extractor reports quality_ok true for the produced document
    And the pptx extractor's version string is non-empty

  @happy_path
  Scenario: Speaker notes flow into the document markdown as blockquotes
    Given the pptx extractor is registered under the name "pptx"
    And the operator has a scripted three slide presentation
    When the operator invokes the pptx extractor's extract method on the bytes
    Then the pptx document markdown contains the speaker notes blockquote

  @error
  Scenario: Pptx refuses a plain text mime type
    Given the pptx extractor is registered under the name "pptx"
    When the operator asks the pptx extractor whether it can extract mime "text/plain"
    Then the pptx extractor does not claim the mime type

  @error
  Scenario: An empty deck fails quality_ok and triggers escalation
    Given the pptx extractor is registered under the name "pptx"
    And the operator has a scripted empty presentation
    When the operator invokes the pptx extractor's extract method on the bytes
    Then the pptx extractor reports quality_ok false for the produced document
