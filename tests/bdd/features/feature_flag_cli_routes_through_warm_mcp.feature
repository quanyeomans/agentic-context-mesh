@feature_flag @cli_routes_through_warm_mcp
Feature: Operator toggles the warm-MCP text-mode CLI routing flag
  As an operator running kairix CLI subcommands
  I want to choose whether text-mode invocations route through the warm MCP worker
  So that I can validate the new shortcut before cutover and roll back safely if needed

  The flag gates the dispatcher at the registry-then-route boundary. When
  OFF, text-mode CLI invocations fall through to the in-process path even
  when a composer is registered and MCP is responsive. When ON, the
  dispatcher routes via warm MCP and renders text from the envelope.
  JSON-mode routing was always enabled and is NOT gated by this flag.
  See docs/architecture/feature-flag-architecture.md §7.

  @happy_path @off
  Scenario: Flag OFF — text-mode CLI falls through to in-process
    Given the operator has the warm-mcp text-mode flag set to false
    When the operator runs a text-mode subcommand with a registered composer
    Then the warm MCP tool call is not made
    And the dispatcher returns none

  @happy_path @on
  Scenario: Flag ON — text-mode CLI routes through warm MCP
    Given the operator has the warm-mcp text-mode flag set to true
    When the operator runs a text-mode subcommand with a registered composer
    Then the warm MCP tool call is made
    And the dispatcher renders the envelope as text
