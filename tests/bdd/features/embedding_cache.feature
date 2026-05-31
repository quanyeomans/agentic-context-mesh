Feature: Persistent embedding cache shields operators from paying twice for the same chunks
  As a kairix operator
  I want the embed pipeline to remember every vector it has received from the provider
  So that a crash mid-run, a re-embed, or a vec-index rebuild never re-burns provider $

  Background:
    Given an embedding cache backed by a fresh SQLite file

  Scenario: A vector written via the cache comes back equal
    Given a vector for chunk hash "h1" under model "m" at dimension 4
    When I put the vector into the cache
    And I read the vector back from the cache
    Then the cache returns the same vector

  Scenario: A repeat embed run hits the cache and skips the provider
    Given a corpus of 3 chunks
    And an empty cache
    When the operator runs the embed pipeline once with a counting provider
    And the operator runs the embed pipeline a second time with a counting provider
    Then the second run dispatches zero provider calls

  Scenario: A partial cache forwards only the misses to the provider
    Given a corpus of 4 chunks
    And the cache already holds vectors for 2 of those chunks
    When the operator runs the embed pipeline with a counting provider
    Then the provider sees exactly 2 chunks
    And the cache now holds vectors for all 4 chunks

  Scenario: Switching the model leaves the previous model's cache slice untouched
    Given the cache holds a vector for chunk "h1" under model "old-model" at dimension 4
    When a put writes a vector for chunk "h1" under model "new-model" at dimension 4
    Then the cache holds a separate vector under each model name
