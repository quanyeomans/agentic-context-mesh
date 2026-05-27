# SharePoint connector — per-drive path filtering (design)

**Status:** design checkpoint awaiting review. Feeds KFEAT-022 (guided configuration) as the prerequisite that unlocks "pick a folder, not a whole drive" for the production deployment.

## Why this is needed now

The production SharePoint discovery surfaced a 126 GB drive (`Mixed-Content-Drive / Documents`) where ~95% of the volume is Microsoft-supplied partner material (`/Vendor-Bulk-Materials`) that the operator doesn't want indexed. The remaining ~5 GB (`/Curated-Content`, `/Shared Documents`) is the actual content worth indexing. The current connector's only scope unit is the drive — pointing at this drive means ingesting all 126 GB or moving content around in SharePoint.

The pattern generalises: real-world drives mix curated and bulk content. Per-folder scope is the missing surface.

This is also a prerequisite for KFEAT-022 piece 3 (volumetric discovery) — the discovery output needs to walk one level deeper than drives so it can show per-folder breakdowns and let the operator pick. The filter is what discovery's output becomes after selection.

## What it does

Two new optional fields on `SharePointDriveSpec`:

```yaml
topology_v2:
  connectors:
    - id: sharepoint-example-drive-conn
      kind: sharepoint
      connector_specific_config:
        drives:
          - drive_id: "b!..."
            include_paths: ["/Curated-Content", "/Shared Documents"]
            exclude_paths: ["/Curated-Content/draft"]
```

**Semantics (locked):**

- **Absent or empty `include_paths`** → current behaviour (whole drive walked). Backward-compatible.
- **Non-empty `include_paths`** → only items whose path starts with one of the listed paths are emitted as change events. Multiple entries combine as a union.
- **`exclude_paths`** → items whose path starts with any excluded path are dropped, even if they matched an include. Exclude wins.
- **Standalone `exclude_paths`** (no include) → all items walked, those matching an exclude dropped.
- **Segment-boundary match** → `/Curated-Content` matches `/Curated-Content/foo.md` but NOT `/Curated-Content-Backup/foo.md`. Implementation: compare against `<path> + "/"` for descendants and exact match for the folder itself.
- **Case** — paths normalised to operator's input casing; comparison is case-insensitive (SharePoint paths are case-preserving but case-insensitive in API).
- **Missing folder** at startup → warn (one-shot Graph lookup at connector init), continue. Don't fail — the folder may be created later.
- **Cursor unchanged** — filter is per-tick application, not persisted state. Operators can change filter values without invalidating the per-drive deltaLink.

**Non-goals (v1):**

- Per-folder sensitivity tier (operators wanting that split into multiple connector instances with different `default_sensitivity`)
- Glob / regex patterns — straight prefix match only (extend later if needed)
- Move-out tombstone synthesis — when an item moves OUT of an included path between sync passes, the connector emits no event for the move. Operators relying on move detection should use a stricter drive-level scope. Captured as known limitation.
- Per-include-path delta optimisation — v1 walks the full drive delta and post-filters. Sub-optimal on huge drives with small includes, but correct. Future: per-folder walks when include set is small.

## Behavioural alignment audit

Walked the existing SharePoint test surface and identified what shifts vs what's new.

### Existing tests that remain unchanged

| File | Why unchanged |
|---|---|
| `tests/contracts/test_sharepoint_protocol.py` | Uses `SharePointDriveSpec(drive_id=_DRIVE_ID)` — new fields have defaults, constructor calls stay valid. Protocol shape is preserved (no method signatures change). |
| `tests/bdd/features/connector_sharepoint.feature` | All scenarios use the default (no filter) → exact same behaviour. |
| `tests/bdd/features/feature_flag_connector_sharepoint.feature` | Flag toggle is orthogonal to filter behaviour. |
| `tests/bdd/features/feature_flag_topology_v2_sharepoint.feature` | Per-drive containers are orthogonal to filter; both can co-exist. |
| `tests/e2e/test_composed_connector_sharepoint_path.py` | Uses fixture data with no filter — unchanged. |
| `tests/e2e/test_composed_topology_v2_sharepoint_path.py` | Same. |
| `tests/integration/test_feature_flag_connector_sharepoint.py` | Filter is config-driven, not flag-driven — flag tests unaffected. |
| `tests/connectors/sharepoint/test_connector.py` | All existing tests use default (no filter); they're exercising the no-filter branch of the new code. |

No existing test needs to break or be re-written. The change is additive.

### New tests (this feature)

| Surface | File | What it adds |
|---|---|---|
| BDD | `tests/bdd/features/connector_sharepoint_path_filtering.feature` | 13 scenarios across operator / pipeline / upstream / agent perspectives (already drafted) |
| BDD steps | `tests/bdd/steps/connector_sharepoint_path_filtering_steps.py` | Step impls; all flow through `make_connector(...)` per F46 — no direct `*Pipeline(...)` construction |
| BDD shim | `tests/bdd/test_connector_sharepoint_path_filtering.py` | `@scenario` shims (per the `tests/bdd/test_*.py` convention used elsewhere) |
| Contract | `tests/contracts/test_sharepoint_protocol.py` | One new test: Protocol surface holds with filter active. Filter is post-processing — the connector still satisfies `SourceConnector` + `CheckpointedConnector` regardless of include/exclude config. Pairs `FakeSharePointConnector` (no filter — Fake doesn't need to model filter) with the real connector (filter active) to prove both shape the Protocol the same way. |
| Integration | `tests/integration/test_feature_flag_connector_sharepoint.py` | Two new tests: (a) filter ON + flag ON → only included items; (b) filter OFF + flag ON → all items. Same flag-branch shape as existing, with filter as an inner parameter. |
| E2E | `tests/e2e/test_composed_connector_sharepoint_path.py` | Extend (don't add new file): one additional `@pytest.mark.e2e` test exercising `factory.build_connector` with a filter-active config + ingest + query, asserting only the included subset is searchable. |
| Unit | `tests/connectors/sharepoint/test_connector.py` | Six new tests covering: prefix-boundary match, exact-folder match, union of multiple includes, exclude overrides include, standalone exclude, missing-folder warning at init |
| Unit (graph_client) | `tests/connectors/sharepoint/test_graph_client.py` | One new test asserting `DriveItemRef.parent_path` is populated from the Graph envelope's `parentReference.path` field |

Per F54: filter behaviour is config-driven, not flag-driven, so F54's flag-both-branch requirement doesn't apply — the connector_sharepoint flag both-branch coverage already exists. The new tests cover filter-both-state (active vs inactive) as ordinary test discipline.

Per F45: no new CLI subcommand, MCP tool, or plugin lands here — the BDD feature file is the documentation contract but doesn't trigger F45's "new capability must ship with BDD" gate. (F45 fires only on net-new capabilities; this is an enhancement to an existing one.) The feature file lands anyway for behavioural traceability.

## Implementation contract

Five code surfaces change. Each is small and additive.

### 1. `kairix/connectors/sharepoint/graph_client.py`

`DriveItemRef` gains a new field carrying the item's parent path. The Graph envelope already includes this under `parentReference.path` (format: `/drives/<drive-id>/root:/Curated-Content/foo`). Extract it during the existing `_drive_item_from` helper, normalise to the operator-facing relative form (`/Curated-Content/foo` — strip the `/drives/<id>/root:` prefix).

```python
@dataclass(frozen=True)
class DriveItemRef:
    item_id: str
    drive_id: str
    name: str
    mime: str | None
    web_url: str | None
    size: int | None
    last_modified_at: str | None
    removed: bool
    parent_path: str | None = None   # NEW — normalised path under the drive root
```

Default `None` for backward compat with any code constructing `DriveItemRef` directly (test fixtures, fakes). Adding a field with default doesn't break F42 (frozen dataclass).

### 2. `kairix/connectors/sharepoint/connector.py` — `SharePointDriveSpec`

Two new fields, both default-empty:

```python
@dataclass(frozen=True)
class SharePointDriveSpec:
    drive_id: str
    site_id: str | None = None
    display_name: str | None = None
    include_paths: tuple[str, ...] = ()      # NEW
    exclude_paths: tuple[str, ...] = ()      # NEW
```

`tuple[str, ...]` not `list` — frozen dataclass needs hashable field types.

### 3. `kairix/connectors/sharepoint/connector.py` — filter helper

New module-level pure function. Two callers (in `list_changes`'s per-drive loop, and in the startup warning probe):

```python
def _path_passes_filter(
    item_path: str | None,
    *,
    include_paths: tuple[str, ...],
    exclude_paths: tuple[str, ...],
) -> bool:
    """Return True when the item's path should be emitted.

    Segment-boundary match — '/Foo' matches '/Foo/bar' and '/Foo' itself
    but NOT '/Foo-Backup/...'. Case-insensitive.

    Empty include_paths means "include everything". Non-empty include_paths
    means "include only items matching at least one entry". exclude_paths
    drops matches regardless of include. Exclude wins.

    Path missing (None) treated as "no path known" — included only when
    include_paths is empty; otherwise dropped (we can't tell if it matches).
    """
```

Six unit tests cover the table cases from `tests/connectors/sharepoint/test_connector.py`.

### 4. `kairix/connectors/sharepoint/connector.py` — apply filter in the drain loop

`list_changes` per-drive loop gains one filter step between `iter_drive_items` and `_item_to_event`:

```python
for spec in self._drives:
    drive_id = spec.drive_id
    start_url = per_drive_cursor.get(drive_id)
    for item in self._graph.iter_drive_items(drive_id, start_url=start_url):
        if not _path_passes_filter(
            item.parent_path + "/" + item.name if item.parent_path else None,
            include_paths=spec.include_paths,
            exclude_paths=spec.exclude_paths,
        ):
            continue
        event = self._item_to_event(item, drive_id=drive_id)
        ...
```

The cursor update logic (lines 274-279) stays as-is — filter is per-tick, cursor reflects the full drain.

### 5. `kairix/connectors/sharepoint/connector.py` — startup warning

New helper called once at `__init__` end, after `self._graph` is wired. For each `SharePointDriveSpec` with non-empty `include_paths`, probe each path via `GET /drives/{drive_id}/root:/<path>`; log a warning naming each that returned 404. Single warning batch per startup — no per-tick re-probe.

This is the only piece that does a Graph round-trip at startup. Scoped behind a try/except so a transient Graph outage at boot doesn't kill connector init.

### 6. `kairix/connectors/sharepoint/connector.py` — `_drive_specs_from_config`

Extend the YAML parser to accept the new fields:

```python
def _drive_specs_from_config(raw: object) -> list[SharePointDriveSpec]:
    # ... existing code accepting list[str] or list[dict] ...
    # For dict entries, also pull include_paths + exclude_paths:
    return [
        SharePointDriveSpec(
            drive_id=entry["drive_id"],
            site_id=entry.get("site_id"),
            display_name=entry.get("display_name"),
            include_paths=tuple(entry.get("include_paths", [])),
            exclude_paths=tuple(entry.get("exclude_paths", [])),
        )
        for entry in raw_normalised
    ]
```

Validation: include/exclude entries must be strings starting with `/`; reject with the standard `fix:` / `next:` shape otherwise.

## Documentation surface

Same commit lands:

- **`kairix.config.example.yaml`** — extend the SharePoint connector block with commented include_paths / exclude_paths examples.
- **`docs/architecture/connector-scope-topology/connector-design-specs/sharepoint.md`** — new `## Path filtering` section pointing to this design doc; updates the "scope" dimension in §1.
- **Operator-facing note + `Vendor-Bulk-Materials` exclude example** — bundled into the next production upgrade note under `docs/upgrades/`.

## Cutover

This is a small additive change behind no new flag — the field defaults preserve the prior behaviour exactly. No cutover protocol needed. Lands on `main` in one commit (six files: `graph_client.py`, `connector.py`, three test files, `kairix.config.example.yaml`), bundled into the next alpha tag.

## Open questions before implementation

1. **Path normalisation when `parentReference.path` is absent.** Some Graph envelope shapes omit the field (e.g. shared item entries). Drop the item from the filter (treating absent path as no-match for non-empty includes), or pass it through? Lean drop — operator who set a filter clearly intends a scope.
2. **Probe vs lazy warning for missing folders.** Probing every include path at startup adds N Graph calls. If a connector has 20+ include paths, that's 20+ requests. Alternative: lazy-warn the first time the filter rejects every item from a drain (suggests the include set is mismatched). Lean probe — happens once per process, surfaces the issue early when operator can act.
3. **`exclude_paths` precedence over `include_paths` when they exactly match.** `include_paths: ["/Foo"]` + `exclude_paths: ["/Foo"]` → drop everything from /Foo? Or treat the conflict as a config error and refuse at parse time? Lean drop-everything — exclude wins matches the documented semantic; refusing at parse time is a harsher UX without a clear benefit.
4. **Default `display_name` when the operator only provides a path.** Today `display_name` defaults to `None` and the connector synthesises a label from `drive_id`. With path filtering, a better default might be `<drive-name> [<first-include-path>]`. Punt — display naming polish doesn't block correctness.

Resolve in review of this doc; implementation locks the choices.
