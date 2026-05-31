"""Unit tests for :mod:`kairix.secrets.naming` — pure-function canonical-name derivation.

Tests cover:
  - canonical_secret_name shape (with and without instance)
  - canonical_env_var derivation (s/-/_/g + uppercase)
  - parse_canonical_name round-trip for single-slot leaves
  - parse_canonical_name tie-break for multi-token instances
  - validation: empty area / leaf, unknown scope, malformed names

All tests are F2-clean (no monkeypatch.setenv) and use no fakes
because the module is a pure-function surface.
"""

from __future__ import annotations

import pytest

from kairix.secrets.naming import (
    canonical_env_var,
    canonical_secret_name,
    parse_canonical_name,
)

pytestmark = pytest.mark.unit


# ── canonical_secret_name ──────────────────────────────────────────


def test_canonical_secret_name_no_instance() -> None:
    """Standard ``kairix-<scope>-<area>-<leaf>`` shape."""
    name = canonical_secret_name("connector", "sharepoint", None, "tenant-id")
    assert name == "kairix-connector-sharepoint-tenant-id"


def test_canonical_secret_name_with_instance() -> None:
    """``kairix-<scope>-<area>-<instance>-<leaf>`` when instance is given."""
    name = canonical_secret_name("connector", "obsidian", "tcv", "encryption-password")
    assert name == "kairix-connector-obsidian-tcv-encryption-password"


def test_canonical_secret_name_normalises_underscores() -> None:
    """Module-style underscores in area (``apple_caldav``) become hyphens."""
    name = canonical_secret_name("connector", "apple_caldav", None, "access")
    assert name == "kairix-connector-apple-caldav-access"


def test_canonical_secret_name_lowercases() -> None:
    """Uppercase area + leaf segments are normalised to lower."""
    name = canonical_secret_name("connector", "GitHub", None, "PAT")
    assert name == "kairix-connector-github-pat"


def test_canonical_secret_name_empty_instance_omitted() -> None:
    """Empty-string instance is treated the same as None."""
    name = canonical_secret_name("provider", "llm", "", "api-key")
    assert name == "kairix-provider-llm-api-key"


def test_canonical_secret_name_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="Unknown scope"):
        canonical_secret_name("widget", "x", None, "y")  # type: ignore[arg-type]  # F3 rationale: deliberately passing an invalid scope to exercise the ValueError path.


def test_canonical_secret_name_rejects_empty_area() -> None:
    with pytest.raises(ValueError, match="area must be"):
        canonical_secret_name("connector", "", None, "y")


def test_canonical_secret_name_rejects_empty_leaf() -> None:
    with pytest.raises(ValueError, match="leaf must be"):
        canonical_secret_name("connector", "x", None, "")


# ── canonical_env_var ──────────────────────────────────────────────


def test_canonical_env_var_no_instance() -> None:
    """``KAIRIX_<SCOPE>_<AREA>_<LEAF>`` — hyphens become underscores; upper."""
    name = canonical_env_var("connector", "sharepoint", None, "tenant-id")
    assert name == "KAIRIX_CONNECTOR_SHAREPOINT_TENANT_ID"


def test_canonical_env_var_with_instance() -> None:
    """Instance segment slots in between area and leaf."""
    name = canonical_env_var("connector", "obsidian", "tcv", "encryption-password")
    assert name == "KAIRIX_CONNECTOR_OBSIDIAN_TCV_ENCRYPTION_PASSWORD"


def test_canonical_env_var_normalises_underscores_in_area() -> None:
    """``apple_caldav`` area normalises to ``APPLE_CALDAV`` — the env-var
    name preserves the canonical hyphen-to-underscore mapping.
    """
    name = canonical_env_var("connector", "apple_caldav", None, "access")
    assert name == "KAIRIX_CONNECTOR_APPLE_CALDAV_ACCESS"


# ── parse_canonical_name (round-trip + tie-break) ──────────────────


def test_parse_canonical_name_no_instance_single_slot_leaf() -> None:
    """Round-trip works cleanly when leaf is a single slot."""
    parsed = parse_canonical_name("kairix-connector-notion-token")
    assert parsed == ("connector", "notion", None, "token")


def test_parse_canonical_name_with_instance_single_slot_leaf() -> None:
    """Round-trip works cleanly when leaf is a single slot + instance present."""
    parsed = parse_canonical_name("kairix-connector-obsidian-tcv-pass")
    assert parsed == ("connector", "obsidian", "tcv", "pass")


def test_parse_canonical_name_tie_break_treats_last_token_as_leaf() -> None:
    """Documented tie-break: last token is leaf; middle becomes instance.

    This is the explicit ambiguity the docstring calls out — the parser
    cannot tell ``kairix-connector-sharepoint-tenant-id`` (instance=None,
    leaf=tenant-id) apart from ``kairix-connector-sharepoint-tenant``
    (instance=tenant, leaf=id). Test pins the documented behaviour so
    consumers know what to expect.
    """
    parsed = parse_canonical_name("kairix-connector-sharepoint-tenant-id")
    assert parsed == ("connector", "sharepoint", "tenant", "id")


def test_parse_canonical_name_multi_token_instance() -> None:
    """Instance can itself contain hyphens; the parser joins everything
    between area and the last token.
    """
    parsed = parse_canonical_name("kairix-connector-slack-alpha-beta-token")
    assert parsed == ("connector", "slack", "alpha-beta", "token")


def test_parse_canonical_name_rejects_missing_prefix() -> None:
    with pytest.raises(ValueError, match="must start with"):
        parse_canonical_name("connector-foo-bar")


def test_parse_canonical_name_rejects_too_short() -> None:
    with pytest.raises(ValueError, match="at least"):
        parse_canonical_name("kairix-connector")


def test_parse_canonical_name_rejects_unknown_scope() -> None:
    with pytest.raises(ValueError, match="Unknown scope"):
        parse_canonical_name("kairix-widget-foo-bar")
