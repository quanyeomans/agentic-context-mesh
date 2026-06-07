Feature: kairix caches reflects warm MCP server state when available
  As a kairix operator
  I want kairix caches to show the warm MCP server's cache state
  So that I see real cache effectiveness instead of zeros from a fresh CLI process

  Scenario: warm MCP — CLI shows server-side cache state
    Given a warm MCP server with non-zero brief_output_cache hits
    When kairix caches is run
    Then stdout shows brief_output_cache with the warm hits count
    And no fall-through banner appears

  Scenario: cold MCP — CLI shows in-process state with banner
    Given no responsive MCP server
    When kairix caches is run
    Then stderr contains the not-responsive banner
    And stdout shows the in-process collectors output

  Scenario: warm MCP --json includes process metadata
    Given a warm MCP server with non-zero brief_output_cache hits
    When kairix caches with the json flag is run
    Then stdout is a valid JSON envelope with caches and process metadata
