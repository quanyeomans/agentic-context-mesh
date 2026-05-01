Feature: Document normalisation pipeline
  As a kairix maintainer
  I want to normalise raw document collections
  So that the reference library has consistent quality

  Scenario: Boilerplate files are filtered
    Given a raw collection with CONTRIBUTING.md and docs/guide.md
    When I run the normalisation pipeline
    Then the output does not contain CONTRIBUTING.md
    And the output contains guide.md

  Scenario: Frontmatter is injected
    Given a document without frontmatter
    When I run the normalisation pipeline
    Then the output has YAML frontmatter with title and source

  Scenario: CC-BY-SA sources are excluded
    Given a source registered as licence tier 4
    When I run normalisation with max_tier 3
    Then that source is excluded from the output
