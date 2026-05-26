@silver @chunker @resilience @failure_mode
Feature: Silver chunker handles pathological inputs without losing content
  As an operator ingesting malformed-but-extractable documents
  I want the chunker to handle oversized paragraphs, sentences, and words
  And to produce chunks within the budget without losing information
  So that a PDF with one giant paragraph doesn't become one giant unsearchable chunk

  Maps to test-resilience-plan.md Class C (chunker/silver boundary cases).
  Bug B (v2026.5.26a1) was the first instance of this class; these scenarios
  prevent regression on the broader class.

  @happy_path
  Scenario: Small markdown under budget produces one chunk
    Given an extracted document with one paragraph 200 characters long
    When the operator passes the document through DefaultSilverProcessor
    Then the resulting chunks number 1
    And no chunk exceeds the 1000-character budget

  @failure_mode
  Scenario: Paragraph 2x chunk budget splits at sentence boundary
    Given an extracted document with one paragraph 2200 characters long
    When the operator passes the document through DefaultSilverProcessor
    Then the resulting chunks number 2 or more
    And no chunk exceeds the 1000-character budget

  @failure_mode
  Scenario: Single sentence over budget splits at word boundary
    Given an extracted document with one 1500-character sentence
    When the operator passes the document through DefaultSilverProcessor
    Then the resulting chunks number 2 or more
    And no chunk exceeds the 1000-character budget

  @failure_mode
  Scenario: Single word over budget splits at character boundary
    Given an extracted document with one 2000-character word
    When the operator passes the document through DefaultSilverProcessor
    Then the resulting chunks number 2 or more
    And no chunk exceeds the 1000-character budget

  @failure_mode
  Scenario: Empty markdown produces zero chunks without crashing
    Given an extracted document with empty markdown
    When the operator passes the document through DefaultSilverProcessor
    Then the resulting chunks number 0
