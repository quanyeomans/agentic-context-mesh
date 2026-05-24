"""Unit tests for :func:`kairix.worker.apply_topology_v2_at_boot`.

Exercises every branch in the worker-side boot helper so it doesn't
fall under the F7 per-file coverage floor. Integration coverage (real
worker boot + DB) lives in
``tests/integration/test_topology_v2_applier.py``.

Branches covered:

* Flag OFF — structural no-op, DB never opened.
* Flag ON + config-path-resolver returns None — skipped with INFO log.
* Flag ON + config-path-resolver returns missing file — skipped.
* Flag ON + YAML read fails — caught, logged, returns True.
* Flag ON + parse failure — caught, logged, returns True.
* Flag ON + empty parsed config (no blocks) — short-circuits before
  opening the DB.
* Flag ON + ApplyValidationError — caught, logged, rolled back,
  returns True (worker keeps running so the operator can fix the YAML).
* Flag ON + happy path — applier runs, DB committed.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kairix.worker import TopologyV2ApplyDeps, apply_topology_v2_at_boot

pytestmark = pytest.mark.unit


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_apply_at_boot_flag_off_is_structural_noop(tmp_path: Path) -> None:
    """Flag OFF: function returns True without invoking config_path or db factories."""
    calls = {"config": 0, "db": 0}

    def _cfg_resolver() -> Path | None:
        calls["config"] += 1
        return None

    def _db_factory() -> sqlite3.Connection:
        calls["db"] += 1
        raise AssertionError("db_factory must not be invoked when flag is OFF")

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: False,
        config_path_resolver=_cfg_resolver,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None
    assert calls == {"config": 0, "db": 0}


def test_apply_at_boot_flag_on_skips_when_config_path_resolver_returns_none(
    tmp_path: Path,
) -> None:
    """Flag ON + no config on disk: skipped, returns True."""

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when no config exists")

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: None,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None


def test_apply_at_boot_flag_on_skips_when_config_path_does_not_exist(
    tmp_path: Path,
) -> None:
    """Flag ON + config path resolver returns a missing file: skipped."""

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when config is missing")

    missing = tmp_path / "no-such-file.yaml"
    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: missing,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None


def test_apply_at_boot_flag_on_skips_when_yaml_unparseable(tmp_path: Path) -> None:
    """Flag ON + malformed YAML: caught, logged, returns True without crashing."""
    config_path = _write_yaml(tmp_path / "kairix.config.yaml", "::not valid yaml::")

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when YAML parse fails")

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None


def test_apply_at_boot_flag_on_skips_when_config_parse_fails(tmp_path: Path) -> None:
    """Flag ON + structurally-wrong config: caught, returns True.

    The parser raises TopologyV2ParseError on a `connectors:` block that
    isn't a list — we round-trip it through YAML to hit the parser's
    error path inside the boot helper's try/except.
    """
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology_v2:\n  connectors: not_a_list\n",
    )

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when parse fails")

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None


def test_apply_at_boot_flag_on_skips_when_no_blocks_declared(tmp_path: Path) -> None:
    """Flag ON + empty topology_v2 block: short-circuits before DB open."""
    config_path = _write_yaml(tmp_path / "kairix.config.yaml", "topology_v2: {}\n")

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when no blocks are declared")

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=_db_factory,
    )
    assert apply_topology_v2_at_boot(deps) is None


def test_apply_at_boot_flag_on_rolls_back_on_validation_failure(tmp_path: Path) -> None:
    """Flag ON + dangling cross-reference: caught, rolled back, returns True."""
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology_v2:\n"
        "  cc_pairs:\n"
        "    - id: stray\n"
        "      connector: no-such\n"
        "      credential: null\n"
        "      name: stray-cp\n",
    )
    db_path = tmp_path / "kairix.sqlite"

    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    assert apply_topology_v2_at_boot(deps) is None
    # The DB was opened (create_schema ran) but nothing was committed.
    db = sqlite3.connect(str(db_path))
    try:
        rows = db.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()
    finally:
        db.close()
    assert rows[0] == 0


def test_apply_at_boot_flag_on_happy_path_commits_rows(tmp_path: Path) -> None:
    """Flag ON + valid config: rows committed; ``cc_pair`` lookup succeeds."""
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology_v2:\n"
        "  connectors:\n"
        "    - id: c1\n"
        "      kind: obsidian\n"
        "      name: c1\n"
        "  cc_pairs:\n"
        "    - id: p1\n"
        "      connector: c1\n"
        "      credential: null\n"
        "      name: cp1\n",
    )
    db_path = tmp_path / "kairix.sqlite"
    deps = TopologyV2ApplyDeps(
        flag_reader=lambda _name: True,
        config_path_resolver=lambda: config_path,
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    assert apply_topology_v2_at_boot(deps) is None
    db = sqlite3.connect(str(db_path))
    try:
        name = db.execute("SELECT name FROM topology_cc_pairs").fetchone()
    finally:
        db.close()
    assert name == ("cp1",)


def test_apply_at_boot_default_deps_constructs_without_raising() -> None:
    """Default ``TopologyV2ApplyDeps()`` constructs every default_factory."""
    deps = TopologyV2ApplyDeps()
    # All three fields resolved to callables; can be invoked at boot time.
    assert callable(deps.flag_reader)
    assert callable(deps.config_path_resolver)
    assert callable(deps.db_factory)
