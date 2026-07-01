Feature: Expand over MCP — an agent reads context around a hit via the tool surface
  As an AI agent calling kairix over MCP
  I want to expand a search hit to its neighbouring chunks
  So that I get the wider context without re-ingesting the whole document

  The MCP expand tool takes the hit's source_uri and seq and returns the
  matched chunk plus its preceding and following chunks within a token
  budget, as a structured envelope.

  Scenario: The expand tool returns the matched chunk with its neighbours
    Given a document indexed as 5 chunks over MCP
    When the agent calls the expand tool at chunk 2
    Then the expand tool envelope includes the matched chunk and its neighbours
    And the expand tool envelope reports no error

  Scenario: The expand tool reports an actionable miss for an unknown chunk
    Given a document indexed as 5 chunks over MCP
    When the agent calls the expand tool at chunk 77
    Then the expand tool envelope says no chunk is stored there

  Scenario: The expand tool resolves a document-level hit from source_uri alone
    A document / section-level hit carries a source_uri but no chunk seq.
    Passing source_uri alone resolves the document's chunks so the handoff
    never dead-ends.

    Given a document indexed as 5 chunks over MCP
    When the agent calls the expand tool with source_uri and no chunk seq
    Then the expand tool envelope returns an ordered window anchored on the first chunk
