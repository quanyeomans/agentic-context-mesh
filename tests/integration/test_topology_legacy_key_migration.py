"""PLA-287 — a legacy topology config key auto-migrates to ``topology`` on read.

The topology operator-config parent key was renamed to ``topology``
(PLA-287). Existing operator configs on disk still carry the old
key; these tests pin that they keep working with zero operator action — the
legacy key is normalized to ``topology`` in memory at the config-load boundary
(``normalize_topology_key``), the operator's file is never rewritten (F94 —
safe on read-only-root deploys), and the setup wizard writes the canonical
``topology`` key going forward.

The legacy key string is never hard-coded in these tests: it comes from the
public ``LEGACY_TOPOLOGY_CONFIG_KEY`` constant (F5 — public surface only) so
the rename's single compat site stays the only place that literal lives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from kairix.config import LEGACY_TOPOLOGY_CONFIG_KEY, TOPOLOGY_CONFIG_KEY, parse_topology
from kairix.config_layers import load_merged_mapping
from kairix.platform.setup.backends import write_config_updates
from kairix.platform.setup.source_oauth import topology_updates_for_source
from kairix.worker import TopologyApplyDeps, apply_topology_at_boot

pytestmark = pytest.mark.integration


# One credential-less source (Obsidian) under the legacy parent key — the
# Customer Zero shape: sources persisted before the rename.
_LEGACY_SECTION: dict[str, Any] = {
    "connectors": [{"id": "obs-main", "kind": "obsidian", "name": "obsidian-main"}],
    "cc_pairs": [
        {"id": "obs-pair", "connector": "obs-main", "credential": None, "name": "obsidian-main-pair"},
    ],
}


# A base that ships the canonical ``topology:`` key (the post-PLA-287 image
# default) with its own placeholder source — the shape an operator upgrades
# INTO while their overlay still carries the legacy ``topology_v2:`` key.
_BASE_CANONICAL_SECTION: dict[str, Any] = {
    "connectors": [{"id": "img-default", "kind": "slack", "name": "image-default"}],
    "cc_pairs": [
        {"id": "img-pair", "connector": "img-default", "credential": None, "name": "image-default-pair"},
    ],
}


def _overlay_env(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A shipped-compose-shaped (overlay_path, env) pair with a read-only base."""
    base = tmp_path / "etc" / "kairix.config.yaml"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("provider: fake_provider\n", encoding="utf-8")
    overlay = tmp_path / "data" / "kairix.config.local.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    env = {
        "KAIRIX_CONFIG_BASE_PATH": str(base),
        "KAIRIX_CONFIG_OVERLAY_PATH": str(overlay),
    }
    return overlay, env


def _applied_cc_pair_names(db_path: Path) -> list[str]:
    db = sqlite3.connect(str(db_path))
    try:
        # F63-bounded: fixture-scale readback of the single row this test seeded.
        rows = db.execute("SELECT name FROM topology_cc_pairs ORDER BY name LIMIT 10").fetchall()
    finally:
        db.close()
    return [row[0] for row in rows]


def test_legacy_config_resolves_through_the_layered_reader(tmp_path: Path) -> None:
    """A legacy-keyed operator config parses to the same topology tree.

    The on-disk file keeps the old key; the layered reader aliases it to the
    canonical key IN MEMORY (never rewriting the file); downstream reads the
    canonical block. Remove the compat branch and the reader/parser reads
    ``topology`` (absent) → an empty config → the source is orphaned and this fails.
    """
    overlay, env = _overlay_env(tmp_path)
    overlay.write_text(yaml.safe_dump({LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}), encoding="utf-8")

    merged = load_merged_mapping(env=env)
    # The reader does NOT rewrite the operator's file — the legacy key survives ON DISK
    # (it is aliased to the canonical key only in the returned in-memory mapping).
    assert LEGACY_TOPOLOGY_CONFIG_KEY in yaml.safe_load(overlay.read_text())

    parsed = parse_topology(merged)
    assert [c.kind for c in parsed.connectors] == ["obsidian"]
    assert [p.name for p in parsed.cc_pairs] == ["obsidian-main-pair"]


def test_legacy_config_drains_at_worker_boot(tmp_path: Path) -> None:
    """A legacy-keyed overlay still materialises its cc_pairs at boot.

    The full composed path: legacy config on disk → ``load_merged_mapping``
    → ``apply_topology_at_boot`` → cc_pair rows in SQLite. Proves a persisted
    legacy-keyed source is not orphaned when the operator upgrades.
    """
    overlay, env = _overlay_env(tmp_path)
    overlay.write_text(yaml.safe_dump({LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}), encoding="utf-8")

    db_path = tmp_path / "kairix.sqlite"
    deps = TopologyApplyDeps(
        config_mapping_fn=lambda: load_merged_mapping(env=env),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    apply_topology_at_boot(deps)

    assert _applied_cc_pair_names(db_path) == ["obsidian-main-pair"]


def test_wizard_migrates_legacy_key_and_writes_the_canonical_key(tmp_path: Path) -> None:
    """The wizard reads a legacy-keyed config, keeps its sources, writes ``topology``.

    The F84 write→read round-trip through the canonical layered reader:
    starting from an operator file on the legacy key with one source,
    connecting a second source emits a ``topology`` block carrying BOTH
    sources, ``write_config_updates`` persists it, and ``load_merged_mapping``
    + the parser sees both — no orphan, and the legacy key is never written.
    """
    existing = {LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}
    updates = topology_updates_for_source("slack", "alpha", ("C1",), existing)

    # The wizard writes ONLY the canonical key going forward.
    assert set(updates) == {"topology"}

    overlay, env = _overlay_env(tmp_path)
    write_config_updates(updates, overlay_path=str(overlay), config_path=None)

    parsed = parse_topology(load_merged_mapping(env=env))
    # The legacy Obsidian source survives AND the new Slack source is added.
    assert {c.kind for c in parsed.connectors} == {"obsidian", "slack"}


def test_single_file_wizard_write_drops_stale_legacy_key(tmp_path: Path) -> None:
    """Single-file (``KAIRIX_CONFIG_PATH``) wizard write drops the stale legacy key (#725).

    An operator's single-file config written before the PLA-287 rename carries
    its sources under the legacy ``topology_v2:`` key. The wizard's write path
    deep-merges the canonical ``topology:`` update into that raw file; without
    the #725 cleanup it leaves BOTH keys — a dead duplicate block plus a
    foot-gun where hand-edits to the still-visible legacy block are silently
    ignored on read.

    This is the F84 write→read round-trip through the canonical layered reader:
    after ``write_config_updates`` the FILE carries ``topology:`` and NOT
    ``topology_v2:``, every non-topology key survives, and BOTH the operator's
    legacy Obsidian source and the new Slack source still resolve through
    ``parse_topology(load_merged_mapping(...))``.

    Sabotage-proof: revert the ``merged.pop(LEGACY_TOPOLOGY_CONFIG_KEY, None)``
    in ``update_config_file`` and the written file keeps the stale legacy key —
    the ``LEGACY_TOPOLOGY_CONFIG_KEY not in on_disk`` assertion fails.
    """
    config_file = tmp_path / "kairix.config.yaml"
    config_file.write_text(
        yaml.safe_dump({"provider": "fake_provider", LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}),
        encoding="utf-8",
    )

    existing = yaml.safe_load(config_file.read_text())
    updates = topology_updates_for_source("slack", "alpha", ("C1",), existing)
    # The wizard writes ONLY the canonical key going forward.
    assert set(updates) == {"topology"}

    # Single-file mode: KAIRIX_CONFIG_PATH is set, no overlay.
    write_config_updates(updates, overlay_path=None, config_path=str(config_file))

    on_disk = yaml.safe_load(config_file.read_text())
    assert TOPOLOGY_CONFIG_KEY in on_disk
    # The stale legacy sibling is gone — no dead duplicate block in the file.
    assert LEGACY_TOPOLOGY_CONFIG_KEY not in on_disk
    # Non-topology keys survive the write untouched.
    assert on_disk["provider"] == "fake_provider"

    # Both sources resolve through the canonical layered reader — the legacy
    # Obsidian source survives (folded into the canonical block) and the new
    # Slack source is added; no orphan.
    parsed = parse_topology(load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config_file)}))
    assert {c.kind for c in parsed.connectors} == {"obsidian", "slack"}
    assert {p.name for p in parsed.cc_pairs} == {"obsidian-main-pair", "slack-alpha"}


def test_upgrade_base_canonical_does_not_shadow_operator_legacy_overlay(tmp_path: Path) -> None:
    """Upgrade path: a fresh base shipping ``topology:`` must not orphan the
    operator's legacy ``topology_v2:`` overlay sources.

    The real Customer-Zero upgrade shape: the new image base ships the
    canonical ``topology:`` key (the example was renamed in PLA-287) while the
    operator's persisted OVERLAY still carries the legacy ``topology_v2:`` key.
    Merging the layers raw leaves BOTH keys in the result, and
    ``normalize_topology_key``'s "canonical wins" rule then drops the operator's
    overlay block on read — every configured source silently orphaned at runtime
    with zero operator action. The layered reader now aliases the legacy key
    per-layer BEFORE the merge, so the operator's overlay overrides the base
    default (normal overlay layering) instead of being shadowed by it.

    Sabotage-proof: revert the per-layer ``normalize_topology_key`` in
    ``load_merged_mapping`` and the operator's ``obsidian-main`` source vanishes
    (only the base ``image-default`` slack source survives) — this test fails.
    """
    base = tmp_path / "etc" / "kairix.config.yaml"
    base.parent.mkdir(parents=True, exist_ok=True)
    # Post-PLA-287 image base ships the canonical topology: key.
    base.write_text(
        yaml.safe_dump({"provider": "fake_provider", "topology": _BASE_CANONICAL_SECTION}),
        encoding="utf-8",
    )
    overlay = tmp_path / "data" / "kairix.config.local.yaml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    # Operator's persisted config still on the legacy key (pre-rename wizard writes).
    overlay.write_text(yaml.safe_dump({LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}), encoding="utf-8")
    env = {"KAIRIX_CONFIG_BASE_PATH": str(base), "KAIRIX_CONFIG_OVERLAY_PATH": str(overlay)}

    parsed = parse_topology(load_merged_mapping(env=env))
    # The operator's overlay (legacy key) overrides the base default — their real
    # source survives; it is NOT shadowed by the base-shipped topology block.
    assert [c.kind for c in parsed.connectors] == ["obsidian"]
    assert [p.name for p in parsed.cc_pairs] == ["obsidian-main-pair"]
