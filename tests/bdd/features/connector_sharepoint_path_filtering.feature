@connector @sharepoint @wave-e @path-filtering
Feature: SharePoint connector scopes per drive by include + exclude paths
  As an operator pointing kairix at a SharePoint drive that holds both
  content worth indexing and content that isn't (Microsoft-supplied
  partner material, archived projects, draft folders), I want to pin
  which top-level folders kairix walks rather than ingesting the whole
  drive.

  Without this, the SharePoint connector's only scope unit is the drive.
  Real-world drives mix curated knowledge with bulk material the operator
  doesn't want indexed — the only workaround today is splitting content
  across drives in SharePoint, which moves the operator burden into the
  source platform.

  Path filtering applies the same prefix-match semantics across every
  stakeholder: an item at /Curated-Content/foo/bar.docx is included by
  include_paths = ["/Curated-Content"] and excluded by exclude_paths =
  ["/Curated-Content/foo"]. Exclude wins when both match.

  Filtering is implemented client-side after the Graph delta drain — the
  Graph delta endpoint doesn't accept a path filter, so we walk every
  item the source returns and drop those that don't match. Drives with
  large excluded sections still pay the listing cost; future work could
  switch to per-folder walks when the include set is small.

  # ─── Operator perspective ───────────────────────────────────────────

  @happy_path @operator
  Scenario: A single include path scopes the drive to one folder
    Given a stubbed Microsoft Graph endpoint returning four envelopes at paths
      | path                                  |
      | /Curated-Content/architecture.md    |
      | /Curated-Content/howto/embed.md     |
      | /Vendor-Bulk-Materials/deck.pptx|
      | /Archived/old-project.pdf         |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then exactly two change events are emitted
    And every emitted item_id corresponds to a path that begins with /Curated-Content/
    And no event references /Vendor-Bulk-Materials/
    And no event references /Archived/

  @operator @union
  Scenario: Multiple include paths combine as a union
    Given a stubbed Microsoft Graph endpoint returning envelopes across three folders
      | path                                  |
      | /Curated-Content/a.md               |
      | /Shared Documents/b.docx              |
      | /Vendor-Bulk-Materials/c.pptx   |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content", "/Shared Documents"]
    Then exactly two change events are emitted
    And the emitted item paths cover both /Curated-Content/a.md and /Shared Documents/b.docx
    And no event references /Vendor-Bulk-Materials/

  @operator @backward_compat
  Scenario: Empty include_paths preserves the current whole-drive behaviour
    Given a stubbed Microsoft Graph endpoint returning four envelopes across mixed folders
      | path                                  |
      | /Curated-Content/a.md               |
      | /Vendor-Bulk-Materials/c.pptx   |
      | /Archived/d.pdf                   |
      | /root-level.txt                       |
    When the operator runs the sharepoint connector with no include_paths configured
    Then exactly four change events are emitted
    And the emitted set is identical to the pre-feature behaviour

  @operator @exclude
  Scenario: Exclude path overrides an overlapping include path
    Given a stubbed Microsoft Graph endpoint returning three envelopes under one parent folder
      | path                                  |
      | /Curated-Content/architecture.md    |
      | /Curated-Content/draft/spike.md     |
      | /Curated-Content/draft/notes.md     |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"] and exclude_paths = ["/Curated-Content/draft"]
    Then exactly one change event is emitted
    And the emitted item_id corresponds to /Curated-Content/architecture.md
    And no event references /Curated-Content/draft/

  @operator @exclude @standalone
  Scenario: Exclude path with no include path still filters
    Given a stubbed Microsoft Graph endpoint returning four envelopes across the drive
      | path                                  |
      | /Curated-Content/a.md               |
      | /Vendor-Bulk-Materials/c.pptx   |
      | /Archived/d.pdf                   |
      | /root-level.txt                       |
    When the operator runs the sharepoint connector with exclude_paths = ["/Vendor-Bulk-Materials", "/Archived"]
    Then exactly two change events are emitted
    And no event references /Vendor-Bulk-Materials/ or /Archived/

  # ─── Edge cases (kairix pipeline) ───────────────────────────────────

  @pipeline @missing_folder
  Scenario: An include path that doesn't exist in the drive warns at startup and skips at runtime
    Given a stubbed Microsoft Graph endpoint returning two envelopes only under /Curated-Content
      | path                                  |
      | /Curated-Content/a.md               |
      | /Curated-Content/b.md               |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content", "/Does-Not-Exist"]
    Then exactly two change events are emitted from /Curated-Content/
    And the startup logs include a warning naming /Does-Not-Exist as not present in the drive
    And no error is raised — the connector continues syncing the present include paths

  @pipeline @exact_folder_match
  Scenario: An include path matches the folder envelope itself plus descendants
    Given a stubbed Microsoft Graph endpoint returning a folder envelope and one nested file
      | path                                  | kind   |
      | /Curated-Content                    | folder |
      | /Curated-Content/nested.md          | file   |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then both the folder envelope and the nested file are included by the filter
    # Folder envelopes are skipped at event emission because the connector
    # only emits file-typed events, but the filter must not pre-emptively
    # drop the folder. (Sibling concern, captured for completeness.)

  @pipeline @prefix_boundary
  Scenario: Prefix matching respects path-segment boundaries
    Given a stubbed Microsoft Graph endpoint returning two envelopes at sibling paths
      | path                                  |
      | /Curated-Content/a.md               |
      | /Curated-Content-Backup/b.md        |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then exactly one change event is emitted
    And the emitted item_id corresponds to /Curated-Content/a.md
    And no event references /Curated-Content-Backup/
    # The filter must not match /Curated-Content-Backup as if it were
    # a sibling of /Curated-Content — segment-boundary match required.

  @pipeline @empty_drive
  Scenario: Filter active on an empty drive emits zero events without error
    Given a stubbed Microsoft Graph endpoint returning no envelopes
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then zero change events are emitted
    And no error is raised

  @pipeline @cursor_unchanged
  Scenario: Cursor format is unaffected by include_paths
    Given a stubbed Microsoft Graph endpoint returning one envelope under /Curated-Content
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then the next cursor encodes the same per-drive deltaLink map as the unfiltered case
    # Filter is a per-tick view, not persisted state — operators can
    # change include_paths without invalidating the cursor.

  # ─── Upstream service (Graph API behaviour) ─────────────────────────

  @upstream @move_out
  Scenario: An item that moved out of an included path between sync passes drops at the filter
    Given a stubbed Microsoft Graph endpoint that returns a follow-up envelope at a new path outside the include set
      | first_pass_path                       | follow_up_path                        |
      | /Curated-Content/page.md            | /Archived/page.md                 |
    When the operator runs the sharepoint connector twice with include_paths = ["/Curated-Content"]
    Then the first pass emits a created event for /Curated-Content/page.md
    And the second pass emits no event for the moved item
    # The connector cannot synthesise a "deleted" event for a move-out
    # without tracking the prior path set itself — explicit non-goal for
    # v1. Operators relying on move detection should use a stricter scope
    # at the drive level. Future enhancement noted.

  @upstream @move_in
  Scenario: An item that moved into an included path emits as a created event
    Given a stubbed Microsoft Graph endpoint returning a delta envelope for an item now under /Curated-Content
      | path                                  | delta_op   |
      | /Curated-Content/moved-in.md        | modified   |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then exactly one change event is emitted
    And the connector emits the event as the Graph delta_op states ("modified")
    # First-pass after move-in surfaces as "modified" because Graph's
    # delta event for a move is a modify, not a create. The orchestrator
    # treats the first-seen item_id as new in either case.

  @upstream @rename_within
  Scenario: A rename within an included path emits modified as usual
    Given a stubbed Microsoft Graph endpoint returning a delta envelope at the renamed path
      | path                                  | delta_op   |
      | /Curated-Content/renamed.md         | modified   |
    When the operator runs the sharepoint connector with include_paths = ["/Curated-Content"]
    Then exactly one modified change event is emitted

  # ─── Agent perspective (LLM via MCP) ────────────────────────────────

  @agent @config_introspection
  Scenario: An LLM-driven setup agent can read the include_paths schema via the MCP config-validate tool
    Given the kairix MCP server is running
    And the operator's kairix.config.yaml declares a sharepoint connector with include_paths set
    When the agent calls tool_config_validate
    Then the response envelope includes the parsed sharepoint connector_specific_config
    And the parsed config preserves the include_paths and exclude_paths values verbatim
    And no validation failure is reported

  @agent @features_status
  Scenario: An LLM-driven status query surfaces the active path filters
    Given the kairix MCP server is running
    And the operator's kairix.config.yaml declares a sharepoint connector with include_paths set
    When the agent calls tool_features_status
    Then the response envelope's connector section names the active include_paths and exclude_paths for the sharepoint cc_pair
    # Agents stand up kairix on a user's behalf — they need to introspect
    # what the user already configured so they don't propose duplicate
    # entries.
