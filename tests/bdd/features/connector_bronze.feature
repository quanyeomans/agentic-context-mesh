@connector @bronze @wave-1-stub
Feature: Bronze store for raw connector payloads
  As an operator running a connector pipeline
  I want every fetched payload to be persisted verbatim before processing
  So that I can replay extraction without re-fetching from the source

  Scenario: Bronze writes raw bytes to filesystem with a pointer record
    Given a configured connector source named "alpha-source"
    And the source returns one payload of raw bytes for identifier "note-001"
    When the pipeline writes the payload to the bronze store
    Then the raw bytes are stored on the filesystem under the bronze root
    And a pointer record links "note-001" to its filesystem location
    And the pointer record carries the source name and the fetch timestamp

  @replay
  Scenario: Bronze replay returns records in fetch order
    Given the bronze store holds three records for "alpha-source" written in order "a", "b", "c"
    When the operator replays the bronze records for "alpha-source"
    Then the replay yields the records in the order "a", "b", "c"
