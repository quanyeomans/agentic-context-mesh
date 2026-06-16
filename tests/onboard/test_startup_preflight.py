"""Unit tests for kairix.platform.onboard.startup_preflight (#449).

Table-drives ``preflight_startup_credentials`` over the placeholder
sentinels (old ``your-api-key-here``, new ``PASTE-...``, empty, unset),
the legacy var pair, neo4j-empty-with-URI (fatal) vs without-URI (no
failure) vs LLM-placeholder (warn), and the all-good case.

Every failure is asserted to name only the offending VAR NAME and never
a credential value (F15 — sabotage-proven below: a real-looking key is
placed in env and asserted to never appear in any failure string).

No monkeypatch / setenv (F1/F2): the preflight takes ``env`` as an
explicit mapping, so each case passes its own dict.
"""

from __future__ import annotations

import pytest

from kairix.platform.onboard.startup_preflight import (
    StartupCredentialFailure,
    is_placeholder,
    preflight_startup_credentials,
)

pytestmark = pytest.mark.unit

# F5 keeps tests off internal ``_x`` imports; these are the PUBLIC env-var
# names (the operator-facing contract), the same literals the rest of the
# suite references (e.g. tests/unit/test_secrets_store.py). The single
# source of truth for the production code is still onboard.check's
# ``_CANONICAL_SECRETS`` (F85) — startup_preflight imports it there.
_CANON_KEY = "KAIRIX_PROVIDER_LLM_API_KEY"  # pragma: allowlist secret — env-var NAME, not a credential
_CANON_ENDPOINT = "KAIRIX_PROVIDER_LLM_ENDPOINT"
_LEGACY_KEY = "KAIRIX_LLM_API_KEY"  # pragma: allowlist secret — env-var NAME, not a credential
_LEGACY_ENDPOINT = "KAIRIX_LLM_ENDPOINT"

# A real-looking credential value used to prove F15: it must never appear
# in any failure string the preflight emits.
_REAL_KEY = "sk-live-9f8a7b6c5d4e3f2a1b0c-SECRET-VALUE"  # pragma: allowlist secret — fake fixture
_REAL_ENDPOINT = "https://prod-resource.openai.azure.com"


def _all_good_env() -> dict[str, str]:
    return {
        _CANON_KEY: _REAL_KEY,
        _CANON_ENDPOINT: _REAL_ENDPOINT,
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "kairix-local-dev",  # pragma: allowlist secret — fake fixture
    }


@pytest.mark.parametrize(
    "placeholder_value",
    [
        "",
        "your-api-key-here",
        "PASTE-YOUR-LLM-KEY-HERE",
        "paste-your-llm-key-here",
        "  your-api-key-here  ",
        "CHANGEME",
    ],
)
def test_is_placeholder_recognises_sentinels(placeholder_value: str) -> None:
    assert is_placeholder(placeholder_value) is True


def test_is_placeholder_treats_unset_as_placeholder() -> None:
    assert is_placeholder(None) is True


@pytest.mark.parametrize("real_value", [_REAL_KEY, _REAL_ENDPOINT, "gpt-4o-mini-real"])
def test_is_placeholder_passes_real_values(real_value: str) -> None:
    assert is_placeholder(real_value) is False


def test_all_good_env_yields_no_failures() -> None:
    assert preflight_startup_credentials(_all_good_env()) == []


@pytest.mark.parametrize(
    "key_value",
    ["your-api-key-here", "PASTE-YOUR-LLM-KEY-HERE", ""],
)
def test_placeholder_canonical_key_warns(key_value: str) -> None:
    env = _all_good_env()
    env[_CANON_KEY] = key_value
    failures = preflight_startup_credentials(env)

    warns = [f for f in failures if f.severity == "warn"]
    assert len(warns) == 1, f"expected one warn, got {failures}"
    failure = warns[0]
    assert failure.var_name == _CANON_KEY
    assert "vector" in failure.reason.lower()
    assert "BM25" in failure.reason or "bm25" in failure.reason.lower()


def test_unset_llm_pair_warns() -> None:
    """LLM vars entirely absent → one warn naming the canonical key."""
    env = {
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "kairix-local-dev",  # pragma: allowlist secret — fake fixture
    }
    failures = preflight_startup_credentials(env)
    assert [f.severity for f in failures] == ["warn"]
    assert failures[0].var_name == _CANON_KEY


def test_legacy_pair_with_real_values_does_not_warn() -> None:
    """The legacy KAIRIX_LLM_* pair still counts as present when real."""
    env = {
        _LEGACY_KEY: _REAL_KEY,
        _LEGACY_ENDPOINT: _REAL_ENDPOINT,
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "kairix-local-dev",  # pragma: allowlist secret — fake fixture
    }
    assert preflight_startup_credentials(env) == []


def test_placeholder_endpoint_with_real_key_warns() -> None:
    """A real key but placeholder endpoint is still degraded → warn."""
    env = _all_good_env()
    env[_CANON_ENDPOINT] = "https://your-resource.openai.azure.com"
    failures = preflight_startup_credentials(env)
    assert [f.severity for f in failures] == ["warn"]


def test_neo4j_empty_password_with_uri_is_fatal() -> None:
    env = _all_good_env()
    env["KAIRIX_NEO4J_PASSWORD"] = ""
    failures = preflight_startup_credentials(env)

    fatals = [f for f in failures if f.severity == "fatal"]
    assert len(fatals) == 1, f"expected one fatal, got {failures}"
    assert fatals[0].var_name == "KAIRIX_NEO4J_PASSWORD"
    assert "neo4j" in fatals[0].reason.lower()


def test_neo4j_empty_password_without_uri_is_not_a_failure() -> None:
    """No neo4j URI configured → the graph layer is simply off; empty password is fine."""
    env = _all_good_env()
    env["KAIRIX_NEO4J_PASSWORD"] = ""
    del env["KAIRIX_NEO4J_URI"]
    failures = preflight_startup_credentials(env)
    assert [f for f in failures if f.severity == "fatal"] == []


def test_placeholder_llm_and_empty_neo4j_yields_both_severities() -> None:
    env = {
        _CANON_KEY: "your-api-key-here",
        _CANON_ENDPOINT: "https://your-resource.openai.azure.com",
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "",
    }
    failures = preflight_startup_credentials(env)
    severities = sorted(f.severity for f in failures)
    assert severities == ["fatal", "warn"]


def test_failures_are_frozen() -> None:
    env = {"KAIRIX_NEO4J_URI": "bolt://neo4j:7687", "KAIRIX_NEO4J_PASSWORD": ""}
    failures = preflight_startup_credentials(env)
    assert failures, "expected at least one failure"
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError; assert immutability
        failures[0].var_name = "mutated"  # type: ignore[misc]  # frozen dataclass — proving immutability


def test_no_credential_value_appears_in_any_failure_message() -> None:
    """F15 sabotage-proof: a real-looking key in env must never leak into a message.

    We set a real-looking key but a placeholder endpoint (forcing a warn),
    plus an empty neo4j password (forcing a fatal). Then assert the real
    key value appears in NONE of the failure fields.
    """
    env = {
        _CANON_KEY: _REAL_KEY,
        _CANON_ENDPOINT: "PASTE-YOUR-LLM-ENDPOINT-HERE",
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "",
    }
    failures = preflight_startup_credentials(env)
    assert failures, "expected failures to inspect for leaks"

    for failure in failures:
        blob = f"{failure.var_name} {failure.reason} {failure.fix}"
        assert _REAL_KEY not in blob, f"credential VALUE leaked into a failure message: {failure}"
        # The VAR NAMES are allowed (and required) to appear.
        assert _CANON_KEY in blob or "NEO4J" in blob.upper()


def test_failure_messages_carry_action_markers() -> None:
    """Every emitted failure carries at least one F21-style action marker."""
    env = {
        _CANON_KEY: "",
        "KAIRIX_NEO4J_URI": "bolt://neo4j:7687",
        "KAIRIX_NEO4J_PASSWORD": "",
    }
    failures = preflight_startup_credentials(env)
    assert failures
    for failure in failures:
        assert any(marker in failure.fix for marker in ("fix:", "next:", "run:")), failure

    # Type sanity — the public dataclass shape.
    assert isinstance(failures[0], StartupCredentialFailure)
