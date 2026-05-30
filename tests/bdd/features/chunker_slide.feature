@chunker @wave_g1 @slide
Feature: SlideChunker emits one chunk per slide for PPTX-shaped input
  As an operator ingesting PowerPoint decks via SharePoint or Google Drive
  I want the slide chunker to emit one chunk per slide
  And to tag each chunk with its slide number and title
  So that retrieval can cite back to a specific slide of a deck
  And no single chunk merges content across two slides.

  This feature pins the ADR-028 Wave G.1 "PPTX — SlideChunker" rule:
  one slide IS the unit, no overlap, slide number is metadata.

  @happy_path
  Scenario: Operator chunks a multi-slide deck via SlideChunker
    Given the slide chunker is constructed
    And the operator has a scripted three slide deck markdown
    When the operator invokes the slide chunker on the deck markdown
    Then the slide chunker emits one chunk per slide
    And each chunk carries the slide number in its metadata

  @error
  Scenario: Empty input emits no chunks
    Given the slide chunker is constructed
    When the operator invokes the slide chunker on empty text
    Then the slide chunker emits no chunks
