Feature: Expand — an agent reads the context around a search hit
  As an AI agent using kairix
  I want to pull the chunks surrounding a search hit
  So that I get the wider context without re-ingesting the whole document

  After a search hit an agent holds the matched chunk plus its source_uri
  and seq. Expand returns the matched chunk together with its preceding and
  following chunks, up to a token budget, ordered as they appear in the
  document.

  Scenario: An agent expands a hit to its neighbouring chunks
    Given a document indexed as 5 chunks
    When the agent expands the hit at chunk 2 with a generous budget
    Then the response includes the matched chunk and both of its neighbours
    And the matched chunk is flagged as the match
    And the expand response reports no error

  Scenario: A tight token budget narrows the window to the matched chunk
    Given a document indexed as 5 chunks
    When the agent expands the hit at chunk 2 with a budget for one chunk
    Then the response includes only the matched chunk

  Scenario: Expanding an unknown hit reports an actionable miss
    Given a document indexed as 5 chunks
    When the agent expands the hit at chunk 99 with a generous budget
    Then the expand response says no chunk is stored there
