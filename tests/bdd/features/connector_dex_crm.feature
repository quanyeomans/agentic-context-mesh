@connector @dex_crm @wave-5
Feature: Dex CRM connector polls the Dex API for Person and Org signals
  As an operator running kairix against a Dex CRM workspace
  I want every contact, organisation, and relationship update to surface as a change event
  So that the entity graph stays current without an operator-triggered re-sync
  and a missing credential surfaces an actionable error instead of a stack trace.

  The connector polls the Dex API with a static Bearer token resolved
  through the standard kairix secret loader. The cursor is the
  ISO-8601 last-modified timestamp. See
  docs/architecture/connector-ingestion-architecture.md Wave 5 and
  docs/architecture/feature-flag-architecture.md §7.

  @happy_path
  Scenario: A configured Dex workspace surfaces one event per changed contact
    Given a Dex CRM workspace with one updated contact since the cursor
    When the operator runs the dex_crm connector list_changes
    Then one modified change event is emitted for the contact
    And the event's item_id encodes the contact kind and id
    And the event's source_link round-trips to an app.getdex.com URL

  @missing_credentials
  Scenario: A missing API key surfaces an actionable error
    Given the connector-dex-api-key secret is not configured
    When the operator runs the dex_crm connector list_changes
    Then the operator sees an actionable error naming the missing secret
