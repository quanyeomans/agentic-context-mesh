"""Soak tier (ADR-024 Bundle F).

Tests under this package carry ``@pytest.mark.soak`` at the module
level so the per-commit gates skip them by default. The nightly
``soak-suite.yml`` workflow runs ``pytest -m soak`` and collects every
test marked soak regardless of directory.
"""
