"""Unit tests for the wizard's ONE config-target resolution (#492).

:func:`kairix.platform.setup.backends.wizard_config_target` is the
single helper both the read side (``read_config_mapping``) and the
write side (``write_config_updates``) resolve through, so the wizard's
read-modify-write cycle can never split across two files. These tests
pin the four-step resolution order and the pip-install XDG default,
plus the paths-reader graceful fallback the layered loader inherits.

Env resolution flows through explicit ``env`` / ``environ`` dicts —
the F2-clean seam — never ``monkeypatch.setenv``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from kairix.config_layers import user_config_path
from kairix.paths import load_top_level_config
from kairix.platform.setup.backends import wizard_config_target

pytestmark = pytest.mark.unit


def test_overlay_path_wins_over_everything(tmp_path: Path) -> None:
    target = wizard_config_target(
        str(tmp_path / "overlay.yaml"),
        str(tmp_path / "single.yaml"),
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert target == tmp_path / "overlay.yaml"


def test_config_path_wins_when_no_overlay(tmp_path: Path) -> None:
    target = wizard_config_target(
        None,
        str(tmp_path / "single.yaml"),
        env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")},
    )
    assert target == tmp_path / "single.yaml"


def test_existing_cwd_config_is_the_legacy_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An install that keeps kairix.config.yaml next to the process keeps it."""
    (tmp_path / "kairix.config.yaml").write_text("provider: fake_provider\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    target = wizard_config_target(None, None, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert target == Path("kairix.config.yaml")


def test_neither_env_set_targets_the_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pip install (no envs, no cwd file) → where ``kairix init`` writes."""
    monkeypatch.chdir(tmp_path)  # guaranteed no kairix.config.yaml in cwd
    target = wizard_config_target(None, None, env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    assert target == tmp_path / "xdg" / "kairix" / "kairix.config.yaml"


def test_xdg_unset_falls_back_to_home_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = wizard_config_target(None, None, env={}, home=tmp_path / "home")
    assert target == tmp_path / "home" / ".config" / "kairix" / "kairix.config.yaml"


def test_user_config_path_prefers_xdg_over_home(tmp_path: Path) -> None:
    assert user_config_path(env={"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, home=tmp_path / "home") == (
        tmp_path / "xdg" / "kairix" / "kairix.config.yaml"
    )
    assert user_config_path(env={}, home=tmp_path / "home") == (
        tmp_path / "home" / ".config" / "kairix" / "kairix.config.yaml"
    )


class _ExplodingMapping(Mapping[str, str]):
    """A Mapping whose iteration raises — drives the reader's fallback."""

    def __getitem__(self, key: str) -> str:
        raise RuntimeError("environment unavailable")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("environment unavailable")

    def __len__(self) -> int:
        return 0


def test_load_top_level_config_returns_none_when_resolution_fails() -> None:
    """The documented contract: resolution failure → None, never a raise."""
    assert load_top_level_config(environ=_ExplodingMapping()) is None
