"""pytest-bdd test module for secrets_cli.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "cli_secrets.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "Verify reports every alias as resolvable when the loader has values")
def test_verify_all_resolvable() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Verify exits non-zero when at least one secret is missing")
def test_verify_one_missing() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Migrate-list emits the legacy-to-canonical mapping as TSV")
def test_migrate_list_tsv() -> None:
    """Body populated by @scenario from the .feature file."""
