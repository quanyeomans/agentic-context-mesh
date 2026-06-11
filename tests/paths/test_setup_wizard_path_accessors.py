"""Unit tests for the setup-wizard paths accessors (#485 / #486).

``config_overlay_path_override`` surfaces ``KAIRIX_CONFIG_OVERLAY_PATH``
to the wizard's config WRITER (the layered loader already reads it);
``container_source_prefill`` resolves the folder-step pre-fill inside a
container. Per F4 the env reads live in ``kairix/paths.py``; tests
drive the parsing through the ``environ=`` injection seam to stay
F2-clean (no ``monkeypatch.setenv("KAIRIX_*")``).
"""

from __future__ import annotations

import pytest

from kairix.paths import config_overlay_path_override, container_source_prefill

pytestmark = pytest.mark.unit


def test_overlay_override_returns_the_configured_path() -> None:
    value = config_overlay_path_override(
        environ={"KAIRIX_CONFIG_OVERLAY_PATH": "/var/lib/kairix/kairix.config.local.yaml"},
    )
    assert value == "/var/lib/kairix/kairix.config.local.yaml"


def test_overlay_override_is_none_when_unset_or_blank() -> None:
    assert config_overlay_path_override(environ={}) is None
    assert config_overlay_path_override(environ={"KAIRIX_CONFIG_OVERLAY_PATH": ""}) is None


def test_container_prefill_defaults_to_the_stock_compose_mount() -> None:
    assert container_source_prefill({"KAIRIX_CONTAINER": "1"}) == "/data/documents"


def test_container_prefill_honours_the_configured_document_root() -> None:
    env = {"KAIRIX_CONTAINER": "1", "KAIRIX_DOCUMENT_ROOT": "/srv/knowledge"}
    assert container_source_prefill(env) == "/srv/knowledge"


def test_container_prefill_is_none_outside_a_container() -> None:
    assert container_source_prefill({}) is None
    assert container_source_prefill({"KAIRIX_DOCUMENT_ROOT": "/srv/knowledge"}) is None
