Feature: Ingest chat transcripts into the knowledge store
  As an operator running kairix as a memory layer for an AI agent
  I want to ingest JSONL chat transcripts as conversation chunks
  And optionally extract facts into the fact store during ingest
  So that the agent can recall the conversation later via search and prep

  Scenario: Happy path — 3 conversations write 3 markdown files
    Given a chat transcript file with 3 conversations of 5 turns each
    When the operator runs ingest-chat against the transcript
    Then 15 turns are reported as ingested
    And 3 conversations are reported as processed
    And 3 markdown files appear under the conversations directory of the document root

  Scenario: No-extract mode skips fact persistence
    Given a chat transcript file with 1 conversation of 5 turns
    And a configured fact extractor that would emit 2 facts per window
    When the operator runs ingest-chat in no-extract mode
    Then 0 facts are persisted in the fact store
    And the conversation markdown is still written to the document root

  Scenario: Idempotent re-ingest does not duplicate writes
    Given a chat transcript file with 1 conversation of 3 turns already ingested once
    When the operator runs ingest-chat against the same transcript again
    Then the markdown file content stays identical to the first ingest
    And the fact store contains no duplicate fact ids

  Scenario: Facts are persisted when extract mode is enabled
    Given a chat transcript file with 1 conversation of 10 turns
    And a configured fact extractor that emits 2 facts per window of 5 turns
    When the operator runs ingest-chat with the default window size
    Then 2 windows are reported as extracted
    And 4 facts are persisted in the fact store
