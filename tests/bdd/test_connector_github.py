"""pytest-bdd test module for connector_github.feature."""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "connector_github.feature")

pytestmark = pytest.mark.bdd


@scenario(FEATURE, "A new commit in a configured repository surfaces as a modified change event")
def test_happy_path_commit_to_event() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "Each repository advances its cursor independently")
def test_cursor_isolation_per_repo() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A webhook delivery with a bad HMAC signature is rejected")
def test_webhook_signature_rejected() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A missing credential surfaces an actionable error")
def test_missing_credential_actionable_error() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "A repos_allowlist restricts the drain to the operator-named repositories")
def test_repos_allowlist_restricts_drain() -> None:
    """Body populated by @scenario from the .feature file."""


@scenario(FEATURE, "An unset repos_allowlist drains every installation-accessible repository")
def test_repos_allowlist_unset_drains_all() -> None:
    """Body populated by @scenario from the .feature file."""
