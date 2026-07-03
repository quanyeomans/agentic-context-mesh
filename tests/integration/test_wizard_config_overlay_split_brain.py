"""#492 — Docker overlay split-brain: every runtime reader sees what the wizard writes.

The setup wizard writes config saves to the operator overlay file
(``KAIRIX_CONFIG_OVERLAY_PATH``) on the shipped compose, but three
runtime readers historically resolved only the read-only base config:
the worker's ``topology_v2`` boot apply, ``paths.load_top_level_config``
(document root), and ``paths.feature_flag_config_overlay``. These tests
pin the contract that wizard-written state is OBSERVED by each reader,
in both branches: overlay mode ON (base + overlay env pair) and OFF
(legacy single-file ``KAIRIX_CONFIG_PATH``), plus the pip-install
default (neither env set → the XDG location ``kairix init`` writes).

All env resolution flows through explicit ``env`` / ``environ`` dicts —
the F2-clean seam — never ``monkeypatch.setenv``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from kairix.config_layers import load_merged_mapping
from kairix.paths import feature_flag_config_overlay, load_top_level_config
from kairix.platform.setup.backends import (
    SetupServiceDeps,
    update_config_file,
    wizard_config_target,
    write_config_updates,
)
from kairix.platform.setup.service import build_setup_service
from kairix.worker import TopologyV2ApplyDeps, apply_topology_v2_at_boot
from tests.fakes import FakePaths

pytestmark = pytest.mark.integration


_TOPOLOGY_UPDATES: dict[str, Any] = {
    "topology_v2": {
        "connectors": [{"id": "github-main", "kind": "github", "name": "github-main"}],
        "cc_pairs": [
            {"id": "p1", "connector": "github-main", "credential": None, "name": "github-main-pair"},
        ],
    }
}


def _overlay_pair(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """A shipped-compose-shaped (base, overlay, env) triple.

    The base plays the read-only image config (it carries ``provider:``
    only); the overlay is the writable file the wizard saves to.
    """
    base = tmp_path / "etc" / "kairix.config.yaml"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("provider: fake_provider\n", encoding="utf-8")
    overlay = tmp_path / "data" / "kairix.config.local.yaml"
    env = {
        "KAIRIX_CONFIG_BASE_PATH": str(base),
        "KAIRIX_CONFIG_OVERLAY_PATH": str(overlay),
    }
    return base, overlay, env


def _applied_cc_pair_names(db_path: Path) -> list[tuple[str]]:
    db = sqlite3.connect(str(db_path))
    try:
        # F63-bounded: fixture-scale readback of the rows this test seeded.
        return db.execute("SELECT name FROM topology_cc_pairs ORDER BY name LIMIT 10").fetchall()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# M5 — update_config_file must deep-merge, not one-level merge
# ---------------------------------------------------------------------------


def test_update_config_file_preserves_nested_sibling_keys(tmp_path: Path) -> None:
    """A 2-deep nested write must not drop sibling keys at depth 2.

    The wizard saves arrive one block at a time (connect GitHub, then
    connect Slack). Each save merges into the SAME overlay file — a
    one-level merge replaces ``topology_v2.credentials`` wholesale on
    the second save, silently dropping the first source's credential.
    """
    target = tmp_path / "kairix.config.local.yaml"
    update_config_file(
        target,
        {"topology_v2": {"credentials": {"github": {"token_env": "GITHUB_TOKEN"}}}},
    )
    update_config_file(
        target,
        {"topology_v2": {"credentials": {"slack": {"token_env": "SLACK_TOKEN"}}}},
    )

    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["topology_v2"]["credentials"]["slack"] == {"token_env": "SLACK_TOKEN"}
    # Pre-#492 this key was dropped: the one-level merge replaced the
    # whole ``credentials`` dict instead of merging inside it.
    assert data["topology_v2"]["credentials"]["github"] == {"token_env": "GITHUB_TOKEN"}


# ---------------------------------------------------------------------------
# Reader B — the worker's topology_v2 boot apply (the #489 drain path)
# ---------------------------------------------------------------------------


def test_wizard_saved_topology_in_overlay_is_applied_at_worker_boot(tmp_path: Path) -> None:
    """Overlay branch ON: the OAuth save's topology drains at next boot.

    Pre-#492 the worker resolved the single base file, so the overlay's
    ``topology_v2:`` was never applied — no cc_pair rows, nothing
    drains, behind a green wizard screen.
    """
    _base, overlay, env = _overlay_pair(tmp_path)
    write_config_updates(_TOPOLOGY_UPDATES, overlay_path=str(overlay), config_path=None)

    db_path = tmp_path / "kairix.sqlite"
    deps = TopologyV2ApplyDeps(
        config_mapping_fn=lambda: load_merged_mapping(env=env),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    apply_topology_v2_at_boot(deps)

    assert _applied_cc_pair_names(db_path) == [("github-main-pair",)]


def test_wizard_saved_topology_in_single_file_mode_is_applied_at_worker_boot(tmp_path: Path) -> None:
    """Overlay branch OFF: legacy single-file installs keep working."""
    config = tmp_path / "kairix.config.yaml"
    write_config_updates(_TOPOLOGY_UPDATES, overlay_path=None, config_path=str(config))

    db_path = tmp_path / "kairix.sqlite"
    deps = TopologyV2ApplyDeps(
        config_mapping_fn=lambda: load_merged_mapping(env={"KAIRIX_CONFIG_PATH": str(config)}),
        db_factory=lambda: sqlite3.connect(str(db_path)),
    )
    apply_topology_v2_at_boot(deps)

    assert _applied_cc_pair_names(db_path) == [("github-main-pair",)]


# ---------------------------------------------------------------------------
# Reader A — paths.load_top_level_config (the folder pick)
# ---------------------------------------------------------------------------


def test_wizard_saved_document_root_in_overlay_is_seen_by_top_level_config(tmp_path: Path) -> None:
    """Overlay branch ON: the picked folder is visible to paths readers.

    Pre-#492 ``load_top_level_config`` read only ``KAIRIX_CONFIG_PATH`` /
    cwd, so an overlay-saved ``paths.document_root`` was ignored.
    """
    docs = tmp_path / "docs"
    docs.mkdir()
    _base, overlay, env = _overlay_pair(tmp_path)
    write_config_updates(
        {"paths": {"document_root": str(docs)}},
        overlay_path=str(overlay),
        config_path=None,
    )

    data = load_top_level_config(environ=env) or {}

    paths_block = data.get("paths")
    assert isinstance(paths_block, dict)
    assert paths_block["document_root"] == str(docs)
    # Sibling keys from the read-only base survive the merge.
    assert data["provider"] == "fake_provider"


def test_wizard_saved_document_root_in_single_file_mode_is_seen_by_top_level_config(
    tmp_path: Path,
) -> None:
    """Overlay branch OFF: legacy single-file installs keep working."""
    docs = tmp_path / "docs"
    docs.mkdir()
    config = tmp_path / "kairix.config.yaml"
    write_config_updates(
        {"paths": {"document_root": str(docs)}},
        overlay_path=None,
        config_path=str(config),
    )

    data = load_top_level_config(environ={"KAIRIX_CONFIG_PATH": str(config)}) or {}

    paths_block = data.get("paths")
    assert isinstance(paths_block, dict)
    assert paths_block["document_root"] == str(docs)


# ---------------------------------------------------------------------------
# Reader C — paths.feature_flag_config_overlay
# ---------------------------------------------------------------------------


def test_feature_flags_in_overlay_are_honoured(tmp_path: Path) -> None:
    """Overlay branch ON: a ``features:`` block in the overlay wins."""
    base, overlay, env = _overlay_pair(tmp_path)
    base.write_text(
        "provider: fake_provider\nfeatures:\n  maintenance_loop: false\n",
        encoding="utf-8",
    )
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text("features:\n  maintenance_loop: true\n", encoding="utf-8")

    assert feature_flag_config_overlay(environ=env) == {"maintenance_loop": True}


def test_feature_flags_in_single_file_mode_are_honoured(tmp_path: Path) -> None:
    """Overlay branch OFF: legacy single-file ``features:`` keeps working."""
    config = tmp_path / "kairix.config.yaml"
    config.write_text("features:\n  maintenance_loop: true\n", encoding="utf-8")

    flags = feature_flag_config_overlay(environ={"KAIRIX_CONFIG_PATH": str(config)})

    assert flags == {"maintenance_loop": True}


# ---------------------------------------------------------------------------
# M4 — pip-install write target (neither env set) + read-side round-trip
# ---------------------------------------------------------------------------


def test_pip_install_saves_land_in_the_xdg_config_home(tmp_path: Path) -> None:
    """Neither env set → saves land where ``kairix init`` writes.

    Pre-#492 the wizard wrote cwd-relative ``kairix.config.yaml`` — a
    file resolved relative to wherever the server process happened to
    start, which ``kairix embed`` run from another directory never saw.
    """
    env = {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "HOME": str(tmp_path / "home"),
    }
    target = wizard_config_target(None, None, env=env)
    assert target == tmp_path / "xdg" / "kairix" / "kairix.config.yaml"

    written = write_config_updates(
        {"provider": "fake_provider"},
        overlay_path=None,
        config_path=None,
        env=env,
    )
    assert written == target
    assert written.is_file()

    # The layered READ side resolves the same file — no split-brain.
    data = load_top_level_config(environ=env) or {}
    assert data["provider"] == "fake_provider"


def test_pip_install_home_fallback_without_xdg(tmp_path: Path) -> None:
    """No XDG_CONFIG_HOME → ``$HOME/.config/kairix/kairix.config.yaml``."""
    env = {"HOME": str(tmp_path / "home")}
    written = write_config_updates(
        {"provider": "fake_provider"},
        overlay_path=None,
        config_path=None,
        env=env,
        home=tmp_path / "home",
    )
    assert written == tmp_path / "home" / ".config" / "kairix" / "kairix.config.yaml"
    data = load_top_level_config(environ=env) or {}
    assert data["provider"] == "fake_provider"


# ---------------------------------------------------------------------------
# save_source guard — env override shadows the picked folder (#492)
# ---------------------------------------------------------------------------


def _guard_service(tmp_path: Path, environ: dict[str, str], writes: list[dict[str, Any]]) -> Any:
    def record_write(updates: Any) -> Path:
        writes.append(dict(updates))
        return tmp_path / "kairix.config.yaml"

    paths = FakePaths(
        document_root=tmp_path / "docs",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    return build_setup_service(
        paths=paths,
        deps=SetupServiceDeps(environ=environ, write_config_fn=record_write),
    )


def test_save_source_rejects_a_pick_shadowed_by_the_env_override(tmp_path: Path) -> None:
    """KAIRIX_DOCUMENT_ROOT set AND ≠ picked folder → actionable reject.

    Pre-#492 the save went through silently, but ``paths.py`` resolution
    lets the env var win regardless — the operator's pick was ignored
    behind a green screen.
    """
    picked = tmp_path / "picked"
    picked.mkdir()
    mounted = tmp_path / "mounted"
    writes: list[dict[str, Any]] = []
    service = _guard_service(tmp_path, {"KAIRIX_DOCUMENT_ROOT": str(mounted)}, writes)

    with pytest.raises(ValueError, match="KAIRIX_DOCUMENT_ROOT") as excinfo:
        service.save_source(str(picked))

    message = str(excinfo.value)
    assert "fix:" in message
    assert str(mounted) in message
    assert writes == []  # nothing was silently written


def test_save_source_accepts_a_pick_matching_the_env_override(tmp_path: Path) -> None:
    """KAIRIX_DOCUMENT_ROOT == picked folder (the stock compose flow) saves."""
    picked = tmp_path / "documents"
    picked.mkdir()
    writes: list[dict[str, Any]] = []
    service = _guard_service(tmp_path, {"KAIRIX_DOCUMENT_ROOT": str(picked)}, writes)

    service.save_source(str(picked))

    assert writes == [{"paths": {"document_root": str(picked)}}]


def test_save_source_without_env_override_saves_normally(tmp_path: Path) -> None:
    """No env override (pip install) → the pick persists unguarded."""
    picked = tmp_path / "picked"
    picked.mkdir()
    writes: list[dict[str, Any]] = []
    service = _guard_service(tmp_path, {}, writes)

    service.save_source(str(picked))

    assert writes == [{"paths": {"document_root": str(picked)}}]


# ---------------------------------------------------------------------------
# Screens name the written file (#492) — save banner + done screen
# ---------------------------------------------------------------------------


def _wizard_client(service: Any) -> Any:
    # starlette ships via the optional [agents] extra (transitive dep of mcp);
    # CI's base-deps stages must skip rather than fail on the missing import.
    pytest.importorskip("starlette")
    from starlette.testclient import TestClient

    from kairix.agents.mcp.transport import build_mcp_app
    from tests.fakes import FakeMcpTransportServer, FakeSecretsLoader

    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_secrets=FakeSecretsLoader(),
        setup_service_factory=lambda: service,
    )
    return TestClient(app, client=("127.0.0.1", 9999))


def test_done_screen_names_the_config_file() -> None:
    """The finish screen tells the operator where their settings live."""
    from tests.fakes import FakeSetupService

    client = _wizard_client(FakeSetupService(config_file="/var/lib/kairix/kairix.config.local.yaml"))
    response = client.get("/setup/done")
    assert response.status_code == 200
    assert "<code>/var/lib/kairix/kairix.config.local.yaml</code>" in response.text


def test_source_saved_screen_names_the_config_file() -> None:
    """The OAuth-source saved banner names the file the topology landed in."""
    from tests.fakes import FakeSetupService

    service = FakeSetupService(config_file="/var/lib/kairix/kairix.config.local.yaml")
    client = _wizard_client(service)
    response = client.post(
        "/setup/source/save",
        data={"provider": "slack", "instance": "alpha", "unit": ["C1"]},
    )
    assert response.status_code == 200
    assert "Saved to" in response.text
    assert "<code>/var/lib/kairix/kairix.config.local.yaml</code>" in response.text


def test_folder_save_guard_renders_the_env_var_banner(tmp_path: Path) -> None:
    """A guarded folder save re-renders the folder screen with the
    KAIRIX_DOCUMENT_ROOT banner instead of a 500."""
    from tests.fakes import FakeSetupService

    service = FakeSetupService(
        save_source_raises=ValueError(
            "This install reads its document folder from the KAIRIX_DOCUMENT_ROOT"
            " environment variable (/data/documents). fix: pick /data/documents."
            " next: scan the folder again, then save."
        ),
    )
    client = _wizard_client(service)
    response = client.post("/setup/folder", data={"folder_path": str(tmp_path)})
    assert response.status_code == 200
    assert "KAIRIX_DOCUMENT_ROOT" in response.text
    assert "fix:" in response.text
    # Indexing must NOT have started on the failed save.
    assert service.start_index_calls == 0
