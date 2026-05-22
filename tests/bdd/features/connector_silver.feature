@connector @silver @wave-1-stub
Feature: Silver processing of extracted documents
  As an operator running a connector pipeline
  I want extracted documents to be chunked and tagged with entity signals
  So that downstream search and entity graph layers see consistent records regardless of source

  Scenario: Silver produces chunks and entity signals from an extracted document
    Given a configured connector source named "alpha-source"
    And an extracted document with text "alpha bravo charlie" and source uri "note://alpha-source/001"
    When the silver processor handles the extracted document
    Then the silver output contains at least one chunk for "note://alpha-source/001"
    And the silver output contains the entity signals discovered in the text
    And each chunk carries the same source uri "note://alpha-source/001"

  @sensitivity
  Scenario: Silver preserves source uri and sensitivity on every chunk
    Given a configured connector source named "alpha-source"
    And an extracted document marked sensitivity "internal" with source uri "note://alpha-source/002"
    When the silver processor handles the extracted document
    Then every chunk in the silver output carries source uri "note://alpha-source/002"
    And every chunk in the silver output carries sensitivity "internal"
