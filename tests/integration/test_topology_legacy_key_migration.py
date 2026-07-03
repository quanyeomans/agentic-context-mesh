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

from kairix.config import LEGACY_TOPOLOGY_CONFIG_KEY, parse_topology
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

    The on-disk file keeps the old key; the layered reader hands it to the
    parser unchanged; the parser normalizes it in memory. Remove the compat
    branch and the parser reads ``topology`` (absent) → an empty config →
    the source is orphaned and this fails.
    """
    overlay, env = _overlay_env(tmp_path)
    overlay.write_text(yaml.safe_dump({LEGACY_TOPOLOGY_CONFIG_KEY: _LEGACY_SECTION}), encoding="utf-8")

    merged = load_merged_mapping(env=env)
    # The reader does NOT rewrite the operator's file — the legacy key survives on disk.
    assert LEGACY_TOPOLOGY_CONFIG_KEY in merged

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
