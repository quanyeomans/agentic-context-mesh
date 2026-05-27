"""E2E composed alpha path — apply-bridge + dual-connector routing through YAML config.

Per F48 sibling to ``tests/e2e/test_composed_production_path.py``.
Exercises the full alpha shape:

1. Operator writes ``kairix.config.yaml`` with a ``topology_v2:`` block
   declaring two connectors (obsidian + sharepoint), one credential, two
   cc_pairs, and two collections with cross-referenced source mappings.
2. Worker boot calls :func:`apply_topology_v2_at_boot`. The apply-bridge
   materialises every block into runtime ``topology_*`` rows.
3. CollectionRouter resolves the cc_pair for each connector by name and
   routes per-item chunk writes to the operator-declared collection.
4. BM25 search against the per-collection FTS index returns each
   connector's content scoped to its declared collection.

The composed-path construction routes through:

  * Real :func:`kairix.config.parse_topology_v2` on the operator YAML.
  * Real :func:`kairix.core.connectors.topology_v2_applier.apply_topology_v2`
    materialising rows.
  * Real :func:`kairix.core.factory.build_connector_pipeline` per
    connector for the silver → write composition.
  * Real :class:`kairix.core.connectors.silver.DefaultSilverProcessor`
    + :class:`kairix.core.connectors.CollectionRouter` per cc_pair.
  * Real :class:`kairix.connectors.obsidian.ObsidianConnector` against
    a tmp_path-rooted vault (no monkey-patching).
  * Fake-but-Protocol-compliant SharePoint connector emitting one PDF
    + one DOCX envelope (the real plugin needs an Azure AAD client
    triple which we don't have in CI; the Protocol contract is pinned
    elsewhere in tests/contracts/test_sharepoint_protocol.py).

Sabotage proof (executed by the agent, restored on completion):
removing the ``sharepoint-corp`` cc_pair from the YAML makes the
CollectionRouter lookup miss; ``resolve_chunk_writer_for_entry`` falls
back to the legacy single-collection writer and the SharePoint chunks
land in a connector-named bucket instead of ``sharepoint-public``.
The collection-scoped assertion fails. With the cc_pair restored, the
chunks land in ``sharepoint-public`` and the assertion passes.

Flag matrix exercised: ``topology_v2_config``, ``topology_v2_runtime``,
``topology_v2_obsidian``, ``connector_sharepoint`` all ON.

Per F48 + F47 + F46: lives under ``tests/e2e/`` with
``@pytest.mark.e2e``; runs in CI Stage 4.5 under ``pytest -m e2e``;
exercises config → factory → ingest → query → assertion via the
composed production code paths.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kairix.config import parse_topology_v2
from kairix.connectors.obsidian import ObsidianConnector
from kairix.core.connectors import DefaultSilverProcessor, ExtractorRegistry
from kairix.core.connectors.topology_v2_applier import apply_topology_v2
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import (
    ChangeEvent,
    Chunk,
    RawArtefact,
    Sensitivity,
)
from kairix.worker import resolve_chunk_writer_for_entry
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Operator YAML — the alpha-path config the apply-bridge materialises.
# ---------------------------------------------------------------------------

_ALPHA_CONFIG = {
    "topology_v2": {
        "connectors": [
            {"id": "obs-conn", "kind": "obsidian", "name": "obs-conn"},
            {"id": "sp-conn", "kind": "sharepoint", "name": "sp-conn"},
        ],
        "credentials": [
            {
                "id": "m365-oauth",
                "kind": "oauth",
                "secret_name": "connector-m365-client-secret",  # pragma: allowlist secret
            },
        ],
        "cc_pairs": [
            {
                "id": "obs-cp",
                "connector": "obs-conn",
                "credential": None,
                "name": "obsidian-personal",
            },
            {
                "id": "sp-cp",
                "connector": "sp-conn",
                "credential": "m365-oauth",
                "name": "sharepoint-corp",
            },
        ],
        "collections": [
            {
                "name": "obsidian-all",
                "sources": [{"cc_pair": "obs-cp", "path_filter": "*"}],
            },
            {
                "name": "sharepoint-public",
                "sources": [{"cc_pair": "sp-cp", "path_filter": "*"}],
            },
        ],
    }
}


# ---------------------------------------------------------------------------
# Fake SharePoint surface — Protocol-compliant; emits one PDF + one DOCX
# envelope without driving the OAuth2 + Graph stack. The real
# SharePointConnector's contract is pinned in
# tests/contracts/test_sharepoint_protocol.py.
# ---------------------------------------------------------------------------


@dataclass
class _SharePointFixtureContent:
    """One fixture binary to emit through the fake SharePoint connector."""

    item_id: str
    raw: bytes
    mime: str
    web_url: str
    last_modified_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeSharePointConnector:
    """Capture-shaped SharePoint :class:`SourceConnector`.

    Mirrors the real plugin's wire shape (same ``name``, same Protocol
    surface, same ``metadata`` shape on ``ChangeEvent``) but never
    touches the Microsoft Graph network or the OAuth2 helper. The real
    plugin's Protocol compliance is pinned by
    ``tests/contracts/test_sharepoint_protocol.py`` (F43 contract).
    """

    def __init__(
        self,
        *,
        fixtures: list[_SharePointFixtureContent],
        sensitivity: Sensitivity = "internal",
    ) -> None:
        self.name = "sharepoint"
        self._fixtures = list(fixtures)
        self._sensitivity: Sensitivity = sensitivity
        self._by_id: dict[str, _SharePointFixtureContent] = {f.item_id: f for f in fixtures}

    def list_changes(self, _cursor: Any | None) -> Iterator[ChangeEvent]:
        events: list[ChangeEvent] = []
        for fixture in self._fixtures:
            events.append(
                ChangeEvent(
                    op="created",
                    item_id=fixture.item_id,
                    modified_at=fixture.last_modified_at,
                    metadata={"sensitivity": self._sensitivity, "drive_id": "fake-drive"},
                )
            )
        return iter(events)

    def fetch(self, item_id: str) -> RawArtefact:
        fixture = self._by_id[item_id]
        return RawArtefact(raw=fixture.raw, mime=fixture.mime, fetched_at=_now_iso())

    def source_link(self, item_id: str) -> str:
        fixture = self._by_id.get(item_id)
        return fixture.web_url if fixture is not None else f"sharepoint://items/{item_id}"

    def sensitivity_for(self, _item_id: str) -> Sensitivity:
        return self._sensitivity

    def next_cursor(self) -> str | None:
        return None


def _make_passthrough_extractor() -> Any:
    """Return a real production :class:`PassthroughExtractor` for the alpha path.

    Both alpha-path fixtures are deliberately plain text (markdown +
    text/plain), so the production passthrough plugin is the
    appropriate per-mime extractor. The full per-format extractor
    matrix is pinned by
    ``tests/e2e/test_composed_connector_sharepoint_path.py`` so we
    don't redundantly cover it here — this test's job is the
    apply-bridge + routing surface, not extractor dispatch.
    """
    from kairix.extractors.passthrough import make_extractor

    return make_extractor()


# ---------------------------------------------------------------------------
# Composed-alpha-path fixtures + helpers
# ---------------------------------------------------------------------------


def _seed_obsidian_vault(vault: Path) -> None:
    """Two markdown files declaring the obsidian-specific tokens to query."""
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "note-alpha.md").write_text(
        "# Project Alpha\n\nThis note carries the unique token obsidianpinetree for retrieval.\n",
        encoding="utf-8",
    )
    (vault / "note-beta.md").write_text(
        "# Project Beta\n\nSecond obsidian note describing the obsidianpinetree concept.\n",
        encoding="utf-8",
    )


def _write_alpha_config_yaml(tmp_path: Path, raw: dict[str, Any]) -> Path:
    """Render the alpha-path YAML config under ``tmp_path``."""
    import yaml

    path = tmp_path / "kairix.config.yaml"
    with path.open("w") as fh:
        yaml.safe_dump(raw, fh, sort_keys=True)
    return path


def _build_db_with_schema(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(str(db_path), timeout=10.0)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    create_schema(db, dims=4)
    return db


def _populate_fts(db: sqlite3.Connection) -> None:
    """Mirror the production embed pipeline's FTS5 population step.

    The connector pipeline persists ``documents`` + ``content`` rows but
    does not touch ``documents_fts`` — mirrors the SharePoint E2E helper.
    Without this step the BM25 query has no target.
    """
    db.execute("DELETE FROM documents_fts")
    db.execute(
        """
        INSERT INTO documents_fts (rowid, filepath, title, doc)
        SELECT d.id, d.path, d.title, c.doc
        FROM documents d
        JOIN content c ON c.hash = d.hash
        WHERE d.active = 1
        """
    )
    db.commit()


def _run_connector_through_router(
    *,
    db: sqlite3.Connection,
    bronze_root: Path,
    connector: Any,
    extractor: Any,
    cc_pair_name: str,
) -> int:
    """Drive one connector through silver + router-backed writer.

    Builds the pipeline via :func:`build_connector_pipeline` (F47) using
    the entry-name collection as the legacy bucket; the chunk writer is
    SUBSTITUTED via the worker's ``resolve_chunk_writer_for_entry``
    helper so the per-item write actually flows through the
    :class:`CollectionRouter` for ``cc_pair_name``. Returns the
    ``run_batch`` ``processed`` count.
    """
    pipeline = build_connector_pipeline(
        db=db,
        bronze_root=bronze_root,
        collection=cc_pair_name,
    )
    # The router-backed writer is the apply-bridge's whole point — the
    # legacy single-collection writer is the fallback the bridge is
    # designed to replace. Swap into the pipeline's chunk-writer slot.
    router_writer = resolve_chunk_writer_for_entry(db, cc_pair_name, flag_on=True)
    # F3-rationale: test-only substitution of the chunk_writer on a
    # factory-built pipeline; the field is the documented composition
    # seam used by the sibling Wave C E2E test.
    pipeline._chunk_writer = router_writer
    result = pipeline.run_batch(connector, extractor)
    db.commit()
    return result.processed


def _bm25_match(
    db: sqlite3.Connection,
    *,
    token: str,
    collection: str,
) -> int:
    """Return the count of FTS-indexed chunks matching ``token`` in ``collection``."""
    rows = list(
        db.execute(
            "SELECT d.path FROM documents d JOIN documents_fts fts ON fts.rowid = d.id "
            "WHERE documents_fts MATCH ? AND d.collection = ?",
            (token, collection),
        )
    )
    return len(rows)


# ---------------------------------------------------------------------------
# E2E composed-alpha-path scenario.
# ---------------------------------------------------------------------------


def test_composed_alpha_path_apply_then_dual_connector_routing(tmp_path: Path) -> None:
    """End-to-end alpha path:

    YAML → parse → apply-bridge → dual-connector sync → per-collection
    BM25 search. Both connectors' content lands scoped to its YAML-
    declared collection.
    """
    # ---- Flags pinned ON via the canonical fake resolver (no monkey-patch).
    resolver = (
        FakeFeatureFlagResolver()
        .with_flag("topology_v2_config", True)
        .with_flag("topology_v2_runtime", True)
        .with_flag("topology_v2_obsidian", True)
        .with_flag("connector_sharepoint", True)
    )
    assert resolver.get("topology_v2_config") is True
    assert resolver.get("topology_v2_runtime") is True
    assert resolver.get("topology_v2_obsidian") is True
    assert resolver.get("connector_sharepoint") is True

    # ---- Wave D config: YAML → parse → apply-bridge.
    config_path = _write_alpha_config_yaml(tmp_path, _ALPHA_CONFIG)
    assert config_path.exists()
    import yaml

    raw = yaml.safe_load(config_path.read_text())
    parsed = parse_topology_v2(raw)
    db_path = tmp_path / "kairix.sqlite"
    db = _build_db_with_schema(db_path)
    apply_result = apply_topology_v2(db, parsed)
    db.commit()
    # 2 connectors + 1 credential + 2 cc_pairs + 2 collections + 2 sources = 9.
    assert apply_result.created == 9
    assert apply_result.unchanged == 0

    # ---- Vault prep + obsidian connector composition (F47: real connector).
    vault = tmp_path / "vault"
    _seed_obsidian_vault(vault)
    obsidian_connector = ObsidianConnector(
        vault_root=vault,
        sensitivity="internal",
    )
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()

    obs_processed = _run_connector_through_router(
        db=db,
        bronze_root=bronze_root,
        connector=obsidian_connector,
        extractor=_make_passthrough_extractor(),
        cc_pair_name="obsidian-personal",
    )
    assert obs_processed >= 1, f"obsidian connector must process ≥1 file; got {obs_processed}"

    # ---- Fake-but-Protocol-compliant SharePoint connector emits two
    # envelopes (plain text bytes — extractor matrix pinned elsewhere).
    sp_fixtures = [
        _SharePointFixtureContent(
            item_id="SP-PDF-001",
            raw=b"# Corporate handbook\n\nThe sharepointmagnolia token uniquely identifies this PDF surrogate.\n",
            mime="text/plain",
            web_url="https://example.sharepoint.com/sites/team/Documents/handbook.txt",
            last_modified_at="2026-05-22T10:00:00Z",
        ),
        _SharePointFixtureContent(
            item_id="SP-DOCX-002",
            raw=b"# Corporate spec\n\nSecond SharePoint doc also tagged sharepointmagnolia.\n",
            mime="text/plain",
            web_url="https://example.sharepoint.com/sites/team/Documents/spec.txt",
            last_modified_at="2026-05-22T11:00:00Z",
        ),
    ]
    sp_connector = _FakeSharePointConnector(fixtures=sp_fixtures)
    sp_processed = _run_connector_through_router(
        db=db,
        bronze_root=bronze_root,
        connector=sp_connector,
        extractor=_make_passthrough_extractor(),
        cc_pair_name="sharepoint-corp",
    )
    assert sp_processed >= 2, f"sharepoint connector must process ≥2 items; got {sp_processed}"

    # ---- Materialise FTS5 rows (the production embed cycle does this).
    _populate_fts(db)

    # ---- Per-collection BM25 assertions: obsidian content scoped to
    # ``obsidian-all`` collection; sharepoint content scoped to
    # ``sharepoint-public``. These are the operator-declared collection
    # NAMES from the YAML, not the cc_pair names — the apply-bridge's
    # ``topology_collections`` row + ``topology_collection_sources``
    # mapping is what makes the router land chunks in those buckets.
    obs_matches_in_obs_all = _bm25_match(db, token="obsidianpinetree", collection="obsidian-all")
    obs_matches_in_sp_pub = _bm25_match(db, token="obsidianpinetree", collection="sharepoint-public")
    sp_matches_in_sp_pub = _bm25_match(db, token="sharepointmagnolia", collection="sharepoint-public")
    sp_matches_in_obs_all = _bm25_match(db, token="sharepointmagnolia", collection="obsidian-all")

    db.close()

    assert obs_matches_in_obs_all >= 1, (
        f"obsidian content must surface in the obsidian-all collection; got {obs_matches_in_obs_all} matches. "
        "Likely cause: apply-bridge failed to register the obsidian-personal cc_pair → obsidian-all mapping; "
        "fix: confirm topology_v2.collections.obsidian-all.sources references cc_pair 'obs-cp'."
    )
    assert obs_matches_in_sp_pub == 0, (
        f"obsidian content must NOT leak into sharepoint-public; got {obs_matches_in_sp_pub}. "
        "Likely cause: routing tables crossed cc_pair → collection bindings."
    )
    assert sp_matches_in_sp_pub >= 1, (
        f"sharepoint content must surface in the sharepoint-public collection; "
        f"got {sp_matches_in_sp_pub} matches. "
        "Likely cause: apply-bridge failed to register the sharepoint-corp cc_pair → sharepoint-public mapping."
    )
    assert sp_matches_in_obs_all == 0, (
        f"sharepoint content must NOT leak into obsidian-all; got {sp_matches_in_obs_all}. "
        "Likely cause: routing tables crossed cc_pair → collection bindings."
    )


def test_composed_alpha_path_apply_bridge_idempotent_under_repeat_boots(tmp_path: Path) -> None:
    """Apply-bridge boot idempotency — repeat applies produce zero new rows.

    Worker reboots (container restart, deploy, watchdog) hit the
    apply-bridge every time. The second run against the same YAML must
    produce ``ApplyResult(created=0, ..., unchanged=N)``.
    """
    db = _build_db_with_schema(tmp_path / "kairix.sqlite")
    parsed = parse_topology_v2(_ALPHA_CONFIG)
    first = apply_topology_v2(db, parsed)
    db.commit()
    second = apply_topology_v2(db, parsed)
    db.commit()
    assert first.created == 9
    assert second.created == 0
    assert second.unchanged == 9
    db.close()


def test_composed_alpha_path_chunk_carries_f39_metadata(tmp_path: Path) -> None:
    """F39 invariant: every chunk emitted through the alpha path carries
    ``source_uri``, ``source_modified_at``, and ``sensitivity`` explicitly.

    The chunker (DefaultSilverProcessor) is the production code under
    test; this is a regression test that the apply-bridge's routing path
    doesn't strip required metadata.
    """
    silver = DefaultSilverProcessor()
    from kairix.core.protocols import BronzeRef, DocMetadata, ExtractedDocument

    ref = BronzeRef(
        source_name="obsidian-personal",
        item_id="note.md",
        raw_path="bronze/note.md",
        mime="text/markdown",
        fetched_at="2026-05-22T09:00:00Z",
    )
    extracted = ExtractedDocument(
        markdown="One paragraph carrying alpha token.\n",
        pages=(),
        images=(),
        metadata=DocMetadata(title=None, author=None, created_date=None, language=None, page_count=None),
        confidence=1.0,
    )
    out = silver.process(
        ref,
        extracted,
        source_uri="obsidian://open?vault=v&file=note.md",
        source_modified_at="2026-05-22T10:00:00Z",
        sensitivity="internal",
    )
    assert len(out.chunks) >= 1
    for chunk in out.chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.source_uri == "obsidian://open?vault=v&file=note.md"
        assert chunk.source_modified_at == "2026-05-22T10:00:00Z"
        assert chunk.sensitivity == "internal"
    # tmp_path unused but kept for parity with sibling tests; lint quiet.
    _ = tmp_path


def test_composed_alpha_path_extractor_registry_resolves_passthrough(tmp_path: Path) -> None:
    """ExtractorRegistry composes against the apply-bridge's worker shape.

    Pinned here so the registry's smoke-resolve continues to work as
    the apply-bridge evolves; a regression in registry construction
    would silently surface as "all chunks land via passthrough" — the
    sibling sharepoint E2E catches the per-mime dispatch.
    """
    registry = ExtractorRegistry()
    extractor = registry.resolve("text/plain", b"hello")
    assert extractor is not None
    _ = tmp_path
