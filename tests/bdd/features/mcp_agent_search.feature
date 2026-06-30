Feature: MCP agent search tool
  As an AI agent calling tool_search
  I want to receive structured search results
  So that I can ground my responses in the knowledge base

  Scenario: Agent receives structured results for a keyword query
    Given the hybrid search returns results for "FEAT-081"
    When the agent calls tool_search with query "FEAT-081 implementation status"
    Then the search response contains a results list
    And the search response intent is "keyword"
    And the search response error is empty
    And each search result has path, score, snippet, and tokens

  Scenario: Agent receives empty results gracefully on unknown topic
    Given the hybrid search returns no results
    When the agent calls tool_search with query "quantum entanglement in marine biology"
    Then the search response contains a results list
    And the search response error is empty

  Scenario: Entity query returns entity graph result first
    Given the hybrid search returns results for "OpenClaw"
    And Neo4j has entity card for "OpenClaw" with summary "AI agent platform"
    When the agent calls tool_search with query "tell me about OpenClaw"
    Then the search response intent is "entity"
    And the first search result source is "entity_graph"
    And the first search result snippet contains "AI agent platform"

  Scenario: Search tool never raises to the caller
    Given the hybrid search raises an error
    When the agent calls tool_search with query "anything"
    Then no search exception was raised
    And the search response is a valid dict with key "error"

  Scenario: Agent requests the cheapest tier and receives a chunk handle for expansion
    Given the hybrid search returns a chunk hit with seq 4 and source uri "m365://drive/report.pdf"
    When the agent calls tool_search with query "report findings" and max tier "L0"
    Then the search retrieval received max tier "L0"
    And the first search result carries seq 4 and source uri "m365://drive/report.pdf"
