@connector @skills @capability-recommender
Feature: Skills connector indexes locally installed Claude Code skills
  As an operator running kairix on a host with installed Claude Code skills
  I want every installed skill, slash-command, and sub-agent to surface as a typed change event
  So that the capability recommender can rank them alongside kairix's own tools
  without me writing per-skill glue, and without errors on a host that has none.

  The connector walks the host's ~/.claude tree (plugins cache plus the flat
  skills folder), parses each artefact's YAML frontmatter, dedups by name
  preferring the higher version, and emits one kind-prefixed change event per
  artefact. There are no credentials. Where ~/.claude is absent the connector
  finds nothing and never errors. See
  docs/architecture/capability-recommender/recommender-mvp-design.md section 3.4.

  @happy_path
  Scenario: An installed skill surfaces as a created change event
    Given a host with one installed skill named "brainstorming"
    When the operator runs the skills connector list_changes with no cursor
    Then one skills change event is emitted
    And the skills change event item id is prefixed with the skill kind
    And the skills change event carries an ISO-8601 modified_at timestamp
    And the skills change event's sensitivity tier is internal

  @render
  Scenario: A fetched skill renders to Markdown
    Given a host with one installed skill named "brainstorming"
    When the operator runs the skills connector list_changes with no cursor
    And the operator fetches the changed skill artefact
    Then the fetched skills artefact is Markdown
    And the fetched skills artefact contains the skill name

  @graceful_degrade
  Scenario: A host with no skills tree yields no events and no error
    Given a host with no installed skills tree
    When the operator runs the skills connector list_changes with no cursor
    Then no skills change events are emitted
