"""
Backwards-compat verification: kairix.vault.health still re-exports everything.

The canonical tests live in tests/store/test_health.py. This file only verifies
that the old import path (kairix.vault.health) continues to work via the shim.
"""

from __future__ import annotations

import warnings

import pytest


@pytest.mark.unit
def test_vault_health_shim_exports_vault_health_report() -> None:
    """VaultHealthReport is available as an alias of StoreHealthReport."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import VaultHealthReport
    r = VaultHealthReport(
        generated_at="2026-01-01T00:00:00Z", neo4j_available=True, organisation_count=1, person_count=1
    )
    assert r.total_entities == 2


@pytest.mark.unit
def test_vault_health_shim_exports_store_health_report() -> None:
    """StoreHealthReport is also re-exported from the shim."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import StoreHealthReport
    r = StoreHealthReport(generated_at="2026-01-01T00:00:00Z")
    assert r.ok is False  # no entities → not ok


@pytest.mark.unit
def test_vault_health_shim_exports_run_vault_health() -> None:
    """run_vault_health is available as alias of run_store_health."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import run_vault_health
    assert callable(run_vault_health)


@pytest.mark.unit
def test_vault_health_shim_exports_run_store_health() -> None:
    """run_store_health is also re-exported from the shim."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import run_store_health
    assert callable(run_store_health)


@pytest.mark.unit
def test_vault_health_shim_exports_format_health_text() -> None:
    """format_health_text is available from the shim."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import StoreHealthReport, format_health_text
    report = StoreHealthReport(
        generated_at="2026-01-01T00:00:00Z", neo4j_available=True, organisation_count=3, person_count=5
    )
    text = format_health_text(report)
    assert "HEALTHY" in text


@pytest.mark.unit
def test_vault_health_shim_vault_and_store_are_same_class() -> None:
    """VaultHealthReport and StoreHealthReport are the same class."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from kairix.vault.health import StoreHealthReport, VaultHealthReport
    assert VaultHealthReport is StoreHealthReport


@pytest.mark.unit
def test_vault_health_shim_emits_deprecation_warning() -> None:
    """Importing from kairix.vault.health should emit a DeprecationWarning."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        import importlib

        import kairix.vault.health

        importlib.reload(kairix.vault.health)
        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(dep_warnings) >= 1
        assert "kairix.store" in str(dep_warnings[0].message) or "deprecated" in str(dep_warnings[0].message).lower()
