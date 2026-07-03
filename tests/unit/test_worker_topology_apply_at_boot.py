"""Unit tests for :func:`kairix.worker.apply_topology_at_boot`.

Exercises every branch in the worker-side boot helper so it doesn't
fall under the F7 per-file coverage floor. Integration coverage (real
worker boot + DB) lives in
``tests/integration/test_topology_applier.py``.

Branches covered:

* Config mapping resolves empty (no config on disk) — skipped with
  INFO log, DB never opened.
* Config mapping resolves empty via a missing / unparseable file —
  skipped (the layered reader degrades to ``{}``).
* Config read raises — caught, logged, DB never opened.
* Parse failure — caught, logged, returns None.
* Empty parsed config (no blocks) — short-circuits before opening
  the DB.
* ApplyValidationError — caught, logged, rolled back, returns None
  (worker keeps running so the operator can fix the YAML).
* Happy path — applier runs, DB committed.

File-driven branches feed real tmp files through
``load_merged_mapping(env={"KAIRIX_CONFIG_PATH": ...})`` — the explicit
env dict is the F2-clean seam, and it exercises the SAME layered read
path production resolves at boot (#492).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kairix.config_layers import load_merged_mapping
from kairix.worker import TopologyApplyDeps, apply_topology_at_boot

pytestmark = pytest.mark.unit


def _write_yaml(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def _mapping_fn_for(config_path: Path) -> Callable[[], dict[str, Any]]:
    """Real-file config mapping seam — legacy single-file resolution."""
    return lambda: load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config_path)})


# NOTE: test_apply_at_boot_flag_off_is_structural_noop was retired alongside
# the topology_config flag (#132). Post-cutover apply_topology_at_boot
# runs unconditionally; the OFF branch no longer exists. The "skip cleanly
# when there's no config on disk" behaviour is now exercised by
# test_apply_at_boot_skips_when_config_mapping_is_empty below.


def test_apply_at_boot_skips_when_config_mapping_is_empty(
    tmp_path: Path,
) -> None:
    """No config on disk (mapping resolves empty): skipped, returns None."""

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when no config exists")

    deps = TopologyApplyDeps(
        config_mapping_fn=dict,
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_skips_when_config_path_does_not_exist(
    tmp_path: Path,
) -> None:
    """KAIRIX_CONFIG_PATH names a missing file: layered read → {} → skipped."""

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when config is missing")

    missing = tmp_path / "no-such-file.yaml"
    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(missing),
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_skips_when_yaml_unparseable(tmp_path: Path) -> None:
    """Malformed YAML: layered read degrades to {} → skipped, no crash."""
    config_path = _write_yaml(tmp_path / "kairix.config.yaml", "::not valid yaml::")

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when YAML parse fails")

    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(config_path),
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_skips_when_config_read_raises(tmp_path: Path) -> None:
    """A raising config read: caught, logged, DB never opened."""

    def _raising_mapping() -> dict[str, Any]:
        raise OSError("disk read failed")

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when the config read raises")

    deps = TopologyApplyDeps(
        config_mapping_fn=_raising_mapping,
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_skips_when_config_parse_fails(tmp_path: Path) -> None:
    """Structurally-wrong config: caught, returns None.

    The parser raises TopologyParseError on a `connectors:` block that
    isn't a list — we round-trip it through YAML to hit the parser's
    error path inside the boot helper's try/except.
    """
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology:\n  connectors: not_a_list\n",
    )

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when parse fails")

    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(config_path),
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_skips_when_no_blocks_declared(tmp_path: Path) -> None:
    """Empty topology block: short-circuits before DB open."""
    config_path = _write_yaml(tmp_path / "kairix.config.yaml", "topology: {}\n")

    def _db_factory() -> sqlite3.Connection:
        raise AssertionError("db_factory must not be invoked when no blocks are declared")

    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(config_path),
        db_factory=_db_factory,
    )
    assert apply_topology_at_boot(deps) is None


def test_apply_at_boot_rolls_back_on_validation_failure(tmp_path: Path) -> None:
    """Dangling cross-reference: caught, rolled back, returns None."""
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology:\n"
        "  cc_pairs:\n"
        "    - id: stray\n"
        "      connector: no-such\n"
        "      credential: null\n"
        "      name: stray-cp\n",
    )
    db_path = tmp_path / "kairix.sqlite"

    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(config_path),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    assert apply_topology_at_boot(deps) is None
    # The DB was opened (create_schema ran) but nothing was committed.
    db = sqlite3.connect(str(db_path))
    try:
        rows = db.execute("SELECT COUNT(*) FROM topology_cc_pairs").fetchone()
    finally:
        db.close()
    assert rows[0] == 0


def test_apply_at_boot_happy_path_commits_rows(tmp_path: Path) -> None:
    """Valid config: rows committed; ``cc_pair`` lookup succeeds."""
    config_path = _write_yaml(
        tmp_path / "kairix.config.yaml",
        "topology:\n"
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
    deps = TopologyApplyDeps(
        config_mapping_fn=_mapping_fn_for(config_path),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    assert apply_topology_at_boot(deps) is None
    db = sqlite3.connect(str(db_path))
    try:
        name = db.execute("SELECT name FROM topology_cc_pairs").fetchone()
    finally:
        db.close()
    assert name == ("cp1",)


def test_apply_at_boot_default_deps_constructs_without_raising() -> None:
    """Default ``TopologyApplyDeps()`` constructs every default_factory."""
    deps = TopologyApplyDeps()
    # Both fields resolved to callables; can be invoked at boot time.
    # ``flag_reader`` retired with the topology_config flag (#132).
    assert callable(deps.config_mapping_fn)
    assert callable(deps.db_factory)
