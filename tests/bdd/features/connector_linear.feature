@connector @linear @wave-5
Feature: Linear connector pulls workspace roadmap and docs via the Linear GraphQL API
  As an operator running kairix against a Linear workspace
  I want every changed roadmap and doc item to surface as a typed change event
  So that issues, projects, documents, initiatives and updates land in the index
  with the right source link and sensitivity without me writing per-entity glue.

  The connector polls five entity types filtered and ordered by updatedAt,
  renders each to Markdown, and emits one type-prefixed change event per
  item. All traffic is HTTPS-only. The default sensitivity tier is
  internal per spec section 1. See
  docs/architecture/connector-scope-topology/connector-design-specs/linear.md.

  @happy_path
  Scenario: A changed Linear issue surfaces as a modified change event
    Given a stubbed Linear workspace that returns one changed issue
    When the operator runs the linear connector list_changes with no cursor
    Then one linear modified change event is emitted
    And the linear change event item id is prefixed with the issue type
    And the linear change event carries an ISO-8601 modified_at timestamp
    And the linear change event's sensitivity tier is internal

  @cursor_advance
  Scenario: The connector advances its high-water-mark cursor after a clean drain
    Given a stubbed Linear workspace that returns one changed issue
    When the operator runs the linear connector list_changes with no cursor
    Then the linear connector exposes a non-empty next cursor
    And the linear next cursor matches the highest updatedAt seen

  @render
  Scenario: A fetched Linear issue renders to Markdown
    Given a stubbed Linear workspace that returns one changed issue
    When the operator runs the linear connector list_changes with no cursor
    And the operator fetches the changed linear issue
    Then the fetched linear artefact is Markdown
    And the fetched linear artefact contains the issue identifier
