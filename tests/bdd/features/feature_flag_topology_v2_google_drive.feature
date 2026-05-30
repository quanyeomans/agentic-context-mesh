@feature_flag @topology_v2_google_drive
Feature: topology_v2_google_drive feature flag gates the Google Drive connector
  As an operator running kairix against a Google Workspace
  I want to gate the Google Drive connector and its per-corpus Container shape behind a feature flag
  So that I can roll the connector out behind the standard cutover protocol
  while still being able to roll back to a no-op default-safe state.

  The flag defaults OFF so existing operators see bit-for-bit current
  behaviour (the google_drive connector slot is a no-op; the Wave B
  shim shape applies to the per-container surface). When ON, the
  worker dispatches sync ticks through the standard connector pipeline,
  the connector emits one Container per configured corpus, the Drive
  changes drain runs per-corpus with the container's own cursor_token,
  load_hierarchy emits a root FOLDER plus one FOLDER child per
  configured corpus parent-before-child, and Resolver.reindex replays
  only the supplied failed item ids instead of re-running a changes
  window.

  @happy_path @off
  Scenario: Flag OFF keeps the connector slot a no-op
    Given a google drive connector configured for two corpora: corpus-alpha, corpus-beta
    And the operator has the topology-v2-google-drive flag set to false
    When the operator calls iter_containers on the google drive connector
    Then two google drive Containers are emitted, one per configured corpus
    When the operator drives list_changes_for_container against google drive corpus corpus-alpha
    Then the legacy single-cursor list_changes branch is observed for google drive
    When the operator calls load_hierarchy on the google drive connector
    Then one root FOLDER node is emitted with no corpus children for google drive

  @happy_path @on
  Scenario: Flag ON emits one Container per configured corpus and isolates per-corpus cursors
    Given a google drive connector configured for two corpora: corpus-alpha, corpus-beta
    And the operator has the topology-v2-google-drive flag set to true
    When the operator calls iter_containers on the google drive connector
    Then two google drive Containers are emitted, one per configured corpus
    And every google drive Container carries access_state ACCESSIBLE with no cursor_token yet
    When the operator calls load_hierarchy on the google drive connector
    Then FOLDER nodes are emitted parent-before-child with a root and one FOLDER child per corpus for google drive
    When the operator calls reindex on the google drive connector with failed ids item-x and item-y
    Then the google drive reindex emits exactly one event per supplied failed id
