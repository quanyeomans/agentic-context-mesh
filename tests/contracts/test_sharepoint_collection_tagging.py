"""Contract test — every SharePoint document written through the
connector framework MUST carry ``collection='sharepoint'`` (GH #371).

Production audit on 2026-06-01 found 1,032,859 docs in the ``default``
collection (most with ``sharepoint://items/...`` source_uri) vs
1,042,746 in ``sharepoint`` — they should all have been in ``sharepoint``.

Root cause: ``kairix.worker._build_reextract_components`` resolved the
chunk-writer collection via ``entry.get("collection", "default")``,
falling back to the literal ``"default"`` when a connector entry didn't
declare an explicit ``collection`` key. Every re-extracted SharePoint
document landed in the wrong collection. ``resolve_collection_for_entry``
is the single-source-of-truth helper that the sync + re-extract paths
now share so the invariant has one edit site.

Contract:
  1. ``resolve_collection_for_entry({"name": "sharepoint"})`` returns
     ``"sharepoint"`` — never the legacy ``"default"`` fallback.
  2. An explicit ``collection`` override is honoured (topology-v2 path).
  3. A missing/empty ``name`` raises a typed ValueError with a fix
     hint — silent ``"default"`` cannot reappear.
  4. The production wiring (``kairix.core.factory.build_connector_pipeline``)
     binds the writer to the supplied collection so 5 SharePoint docs
     across the legitimate paths (initial sync, delta sync, path-filter-
     pass) all land with ``documents.collection = 'sharepoint'``.

Sabotage proof (mutate prod → confirm test FAILS → restore → confirm
test PASSES — executed by the agent; see commit body for the exact
mutation + failure-message capture).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import ChangeEvent
from kairix.worker import resolve_collection_for_entry
from tests.fakes import FakeExtractor, FakeSourceConnector

pytestmark = pytest.mark.contract


_SHAREPOINT = "sharepoint"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Unit-level: the helper that pins the documents.collection invariant
# ---------------------------------------------------------------------------


def test_resolve_collection_for_entry_uses_connector_name() -> None:
    """The connector entry's ``name`` is the canonical collection — never
    the legacy ``"default"`` fallback. This pins the GH #371 fix at the
    single edit site so a future refactor cannot reintroduce the silent
    ``entry.get("collection", "default")`` shape.
    """
    assert resolve_collection_for_entry({"name": _SHAREPOINT}) == _SHAREPOINT


def test_resolve_collection_for_entry_honours_explicit_override() -> None:
    """Operators who pre-declare a typed collection (topology-v2) get
    that name on the legacy writer path. Override wins over the
    fallback to ``name`` — same shape the topology-v2 applier expects."""
    entry = {"name": _SHAREPOINT, "collection": "sharepoint-public-docs"}
    assert resolve_collection_for_entry(entry) == "sharepoint-public-docs"


def test_resolve_collection_for_entry_rejects_missing_name() -> None:
    """An entry without a non-empty ``name`` raises with a typed fix
    hint — the silent ``"default"`` fallback that caused GH #371 cannot
    return through a new code path that forgot to set ``name``.
    """
    with pytest.raises(ValueError, match="missing the 'name' key"):
        resolve_collection_for_entry({"config": {}})


def test_resolve_collection_for_entry_rejects_empty_name() -> None:
    """Empty string is not a valid collection — same fix hint as the
    missing-key shape. Pins the failure mode for operators who set
    ``name: ""`` in YAML.
    """
    with pytest.raises(ValueError, match="missing the 'name' key"):
        resolve_collection_for_entry({"name": ""})


def test_resolve_collection_for_entry_ignores_blank_override() -> None:
    """A blank ``collection`` override falls through to ``name`` rather
    than landing the empty string in the writer. The legacy
    ``entry.get("collection", "default")`` shape would have used the
    empty string verbatim; this helper falls back to the connector name
    so the leak cannot reappear via a blank override.
    """
    assert resolve_collection_for_entry({"name": _SHAREPOINT, "collection": ""}) == _SHAREPOINT


# ---------------------------------------------------------------------------
# Production-wiring: 5 docs across the legitimate SharePoint paths
# ---------------------------------------------------------------------------


def _sharepoint_events() -> list[ChangeEvent]:
    """Five ChangeEvents simulating the legitimate SharePoint paths.

    Two ``created`` from an initial-sync drain, two ``modified`` from a
    delta-sync drain, and one ``created`` from a path-filter-pass on a
    nested folder. Each item_id maps to a distinct document so the
    contract test can count 5 rows in the ``documents`` table.
    """
    return [
        # Initial-sync wave (two docs from the first drain on a drive)
        ChangeEvent(op="created", item_id="initial-doc-1.md", modified_at=_now()),
        ChangeEvent(op="created", item_id="initial-doc-2.md", modified_at=_now()),
        # Delta-sync wave (two modified docs on a later tick)
        ChangeEvent(op="modified", item_id="delta-doc-1.md", modified_at=_now()),
        ChangeEvent(op="modified", item_id="delta-doc-2.md", modified_at=_now()),
        # Path-filter-pass (one new doc inside an included folder)
        ChangeEvent(op="created", item_id="curated-doc-1.md", modified_at=_now()),
    ]


def _drive_through_pipeline(db: sqlite3.Connection, tmp_path: Path) -> None:
    """Push five SharePoint-named events through the production wiring.

    Uses the canonical ``build_connector_pipeline`` factory (F47) bound
    to ``collection="sharepoint"`` so the production wiring's
    ``legacy_chunk_writer`` constructs a real ``_SqliteChunkWriter`` and
    every ``documents`` INSERT carries the explicit collection tag.
    """
    events = _sharepoint_events()
    fake = FakeSourceConnector(
        name=_SHAREPOINT,
        events=events,
        content={ev.item_id: f"# {ev.item_id}\n\nbody for {ev.item_id}".encode() for ev in events},
    )
    pipeline = build_connector_pipeline(
        db=db,
        bronze_root=tmp_path / "bronze",
        collection=_SHAREPOINT,
    )
    pipeline.run_batch(fake, FakeExtractor())
    db.commit()


def test_sharepoint_pipeline_tags_every_doc_with_sharepoint_collection(tmp_path: Path) -> None:
    """The load-bearing invariant — every document produced by the
    SharePoint connector flow lands with ``collection='sharepoint'``.

    Drives five docs across the three legitimate paths (initial,
    delta, path-filter-pass) through the production ``build_connector_pipeline``
    factory. Asserts:
      * Five active documents land in the SQLite ``documents`` table.
      * Every one has ``collection='sharepoint'``.
      * Zero documents leak into ``default``.

    Sabotage proof: mutate ``_SqliteChunkWriter.upsert`` in ``kairix/worker.py``
    to override ``self._collection`` to ``"default"`` immediately before
    each INSERT; the docs-in-sharepoint assertion fails with
    ``5 == 0``; restored, the assertion passes.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    _drive_through_pipeline(db, tmp_path)

    rows = list(
        db.execute(
            "SELECT path, collection FROM documents WHERE active = 1 ORDER BY path",
        )
    )
    # Five docs, every one tagged sharepoint.
    assert len(rows) == 5, f"expected 5 active docs, got {len(rows)}: {rows!r}"
    leaks = [(path, collection) for path, collection in rows if collection != _SHAREPOINT]
    assert not leaks, (
        f"sharepoint docs leaked into wrong collection — every row must carry "
        f"collection='sharepoint'; leaked rows: {leaks!r}"
    )

    # Cross-check the GH #371 production symptom — zero rows in 'default'.
    default_count = db.execute(
        "SELECT COUNT(*) FROM documents WHERE collection = 'default'",
    ).fetchone()[0]
    assert default_count == 0, (
        f"GH #371 leak: {default_count} sharepoint docs landed in collection='default'. "
        f"fix: confirm worker.resolve_collection_for_entry routes both sync + re-extract "
        f"through the connector name, never the legacy 'default' fallback."
    )


def test_sharepoint_pipeline_source_uri_is_sharepoint_scheme(tmp_path: Path) -> None:
    """Backstop for the production audit signal — every sharepoint doc
    not only carries ``collection='sharepoint'`` but also carries a
    ``sharepoint://``-shaped ``source_uri``. This pairs with the
    primary assertion so a future regression that flips the collection
    tag back to ``default`` while keeping the source_uri intact (the
    exact shape GH #371's production audit caught) is caught here too.
    """
    db = sqlite3.connect(":memory:")
    create_schema(db, dims=4)
    _drive_through_pipeline(db, tmp_path)

    rows = list(
        db.execute(
            "SELECT source_uri, collection FROM documents WHERE active = 1",
        )
    )
    for source_uri, collection in rows:
        assert source_uri is not None and source_uri.startswith("sharepoint://"), (
            f"expected sharepoint:// source_uri shape, got {source_uri!r}"
        )
        assert collection == _SHAREPOINT, (
            f"sharepoint-scheme source_uri {source_uri!r} landed in collection={collection!r}; "
            f"this is the exact GH #371 leak shape."
        )
