"""Integration: a multi-line PEM survives the production write → read path.

Review finding H2 (verifier-reproduced): the wizard's GitHub leg ALWAYS
failed after successful consent because ``set_secret`` rejected any
value containing a newline — the GitHub App PEM private key is
multi-line by definition — leaving a half-written credential set. The
``kairix connect`` CLI path was worse: ``FileTokenStore`` wrote the raw
PEM into the line-based bundle, corrupting every parser downstream.

This suite drives the REAL production chain with a real multi-line
PEM-shaped value (fake key material):

    set_secret → bundle file → load_secrets_file (parse + decode)
    → SecretsLoader (the resolver the GitHub connector injects)

and asserts the read-back is byte-identical, the bundle file stays
line-parseable + greppable, and the connector's published leaf names
(``GITHUB_APP_LEAVES``) resolve.

Sabotage-proof (executed, F68 ``returns_partial`` shape): reverted the
encode step in ``set_secret`` (passed ``value`` instead of
``stored_value`` to ``_upsert_line``) —
``test_pem_round_trip_is_byte_identical`` and
``test_pem_write_keeps_every_bundle_line_parseable`` both failed with
corrupted multi-line output. Restored. Pre-fix (on the parent commit)
the whole suite fails at the ``set_secret`` call with "contains a
newline".

F2-clean: every path flows through explicit ``bundle_path=`` /
``env=`` seams — no ``KAIRIX_*`` env mutation. F15: assertions compare
values to the local fixture; nothing is logged or printed.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kairix.connectors.github.connector import GITHUB_APP_LEAVES, GITHUB_LEAF_APP_PRIVATE_KEY
from kairix.secrets import load_secrets_file
from kairix.secrets.loader import SecretsLoader
from kairix.secrets.naming import canonical_env_var, canonical_secret_name
from kairix.secrets.store import set_secret

pytestmark = pytest.mark.integration

# Fake key material — PEM-shaped (multi-line, BEGIN/END markers, a
# backslash to exercise escape handling), NOT a real key.
_FAKE_PEM = (  # pragma: allowlist secret — fake fixture key body
    "-----BEGIN RSA PRIVATE KEY-----\n"  # pragma: allowlist secret — fake fixture marker line
    "FAKE-LINE-ONE-agent-alpha\n"
    "FAKE\\LINE-TWO-with-backslash\n"
    "-----END RSA PRIVATE KEY-----\n"
)

_PEM_NAME = canonical_secret_name("connector", "github", None, GITHUB_LEAF_APP_PRIVATE_KEY)
_PEM_ENV_VAR = canonical_env_var("connector", "github", None, GITHUB_LEAF_APP_PRIVATE_KEY)


def _write_pem_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "kairix.env"
    set_secret(_PEM_NAME, _FAKE_PEM, bundle_path=bundle)
    return bundle


def test_pem_round_trip_is_byte_identical(tmp_path: Path) -> None:
    """write → parse → decode returns the exact bytes that went in."""
    bundle = _write_pem_bundle(tmp_path)
    load_secrets_file.cache_clear()
    parsed = load_secrets_file(bundle)
    assert parsed[_PEM_ENV_VAR] == _FAKE_PEM


def test_pem_resolves_through_secrets_loader_for_connector_leaves(tmp_path: Path) -> None:
    """The hydrated mapping resolves every GitHub App leaf the connector reads."""
    bundle = _write_pem_bundle(tmp_path)
    for leaf, value in (("app-id", "42"), ("installation-id", "70000")):
        set_secret(canonical_secret_name("connector", "github", None, leaf), value, bundle_path=bundle)
    load_secrets_file.cache_clear()
    loader = SecretsLoader(env=dict(load_secrets_file(bundle)))
    resolved = {
        leaf: loader.get(scope="connector", area="github", instance=None, leaf=leaf) for leaf in GITHUB_APP_LEAVES
    }
    assert resolved[GITHUB_LEAF_APP_PRIVATE_KEY] == _FAKE_PEM
    assert resolved["app-id"] == "42"
    assert resolved["installation-id"] == "70000"


def test_pem_write_keeps_every_bundle_line_parseable(tmp_path: Path) -> None:
    """Corruption regression: the PEM lands on ONE ``KEY=VALUE`` line."""
    bundle = _write_pem_bundle(tmp_path)
    lines = bundle.read_text(encoding="utf-8").splitlines()
    assert lines, "bundle unexpectedly empty"
    for line in lines:
        key = line.partition("=")[0]
        assert "=" in line and key.replace("_", "").isalnum(), f"unparseable bundle line: {line!r}"
    # Exactly one line carries the PEM entry, and it stays greppable.
    pem_lines = [line for line in lines if line.startswith(f"{_PEM_ENV_VAR}=")]
    assert len(pem_lines) == 1
    assert "BEGIN RSA PRIVATE KEY" in pem_lines[0]  # pragma: allowlist secret — asserts the fake fixture marker


def test_pem_bundle_file_is_owner_only(tmp_path: Path) -> None:
    """The bundle holding the key is locked to 0600 on write."""
    bundle = _write_pem_bundle(tmp_path)
    assert stat.S_IMODE(os.stat(bundle).st_mode) == 0o600


def test_pem_upsert_preserves_unrelated_entries(tmp_path: Path) -> None:
    """Re-writing the PEM replaces its line; neighbours pass through verbatim."""
    bundle = tmp_path / "kairix.env"
    bundle.write_text("# operator comment\nUNRELATED_VAR=keep-me\n", encoding="utf-8")
    set_secret(_PEM_NAME, _FAKE_PEM, bundle_path=bundle)
    set_secret(_PEM_NAME, _FAKE_PEM, bundle_path=bundle)  # idempotent upsert
    text = bundle.read_text(encoding="utf-8")
    assert text.count(f"{_PEM_ENV_VAR}=") == 1
    assert "# operator comment" in text
    assert "UNRELATED_VAR=keep-me" in text
