"""Unit tests for the MCP layered-readiness capability probe.

The probe shapes onboard-check results into the contract
``/healthz/ready`` expects. These tests pin that shape under five
scenarios — all-pass, secrets-fail, vector-fail, helper-raises, and
result-without-message — by injecting fake check callables via the
``build_capability_probe(secrets_check=..., vector_check=...)`` DI
seam. Production helpers are exercised by their own onboard tests.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kairix.agents.mcp.capability_probe import (
    build_capability_probe,
    secrets_check_with_placeholder_detection,
)

pytestmark = pytest.mark.unit

# F5 keeps tests off internal ``_x`` imports; these are the PUBLIC env-var
# names (the operator-facing contract). Production single-sources them from
# onboard.check's ``_CANONICAL_SECRETS`` (F85).
_CANON_KEY = "KAIRIX_PROVIDER_LLM_API_KEY"  # pragma: allowlist secret — env-var NAME, not a credential
_CANON_ENDPOINT = "KAIRIX_PROVIDER_LLM_ENDPOINT"
_REAL_KEY = "sk-live-abcdef0123456789-REAL"  # pragma: allowlist secret — fake fixture
_REAL_ENDPOINT = "https://prod.openai.azure.com"


@dataclass
class _FakeCheckResult:
    """Minimal shape that matches ``onboard.check.CheckResult``'s protocol."""

    ok: bool
    detail: str = ""


def _ok(detail: str = "") -> _FakeCheckResult:
    return _FakeCheckResult(ok=True, detail=detail)


def _fail(detail: str) -> _FakeCheckResult:
    return _FakeCheckResult(ok=False, detail=detail)


def test_probe_reports_all_capabilities_pass() -> None:
    """Both injected checks return ok=True → probe reports True/True/True with empty detail."""
    probe = build_capability_probe(
        secrets_check=lambda: _ok(),
        vector_check=lambda: _ok(),
    )
    result = probe()

    assert result["secrets_loaded"] is True
    assert result["vector_search_capable"] is True
    assert result["bm25_search_capable"] is True
    assert result["detail"] == {}


def test_probe_surfaces_secrets_failure() -> None:
    """When secrets check fails, the failure message lands in detail['secrets_loaded']."""
    probe = build_capability_probe(
        secrets_check=lambda: _fail("LLM credentials not found"),
        vector_check=lambda: _ok(),
    )
    result = probe()

    assert result["secrets_loaded"] is False
    assert "LLM credentials not found" in result["detail"]["secrets_loaded"]
    assert result["vector_search_capable"] is True
    assert result["bm25_search_capable"] is True


def test_probe_surfaces_vector_search_failure() -> None:
    """A failing vector_search check produces vector_search_capable=False with detail."""
    probe = build_capability_probe(
        secrets_check=lambda: _ok(),
        vector_check=lambda: _fail("Vector search failed (vec_failed=True)"),
    )
    result = probe()

    assert result["vector_search_capable"] is False
    assert "vec_failed" in result["detail"]["vector_search_capable"]


def test_probe_does_not_propagate_check_helper_exceptions() -> None:
    """A check helper raising must NOT escape the probe — it would 500 the load-balancer probe.

    The probe is the caller's only safety net; if it raises, the
    ``/healthz/ready`` endpoint goes down. This contract pins the
    defensive try/except in ``build_capability_probe``.
    """

    def _boom() -> _FakeCheckResult:
        raise RuntimeError("onboard check imploded")

    probe = build_capability_probe(secrets_check=_boom, vector_check=_boom)
    result = probe()

    assert result["secrets_loaded"] is False
    assert result["vector_search_capable"] is False
    assert "onboard check imploded" in result["detail"]["secrets_loaded"]
    assert "onboard check imploded" in result["detail"]["vector_search_capable"]


def test_probe_handles_missing_detail_attribute() -> None:
    """A check result without a ``detail`` attribute still produces a default detail string.

    Defensive: an alternate result shape (e.g. boolean returned from a
    third-party probe) shouldn't crash the JSON encode.
    """

    @dataclass
    class _NoDetailResult:
        ok: bool

    probe = build_capability_probe(
        secrets_check=lambda: _NoDetailResult(ok=False),  # type: ignore[arg-type]  # narrow Protocol — ``detail`` deliberately absent in this shape
        vector_check=lambda: _NoDetailResult(ok=True),  # type: ignore[arg-type]  # narrow Protocol — ``detail`` deliberately absent in this shape
    )
    result = probe()

    assert result["secrets_loaded"] is False
    # Default detail message is non-empty and human-readable.
    assert result["detail"]["secrets_loaded"]


def test_probe_passes_default_when_no_callables_injected() -> None:
    """``build_capability_probe()`` returns a callable that uses production defaults.

    We don't invoke the production callable here (it would touch real
    onboard.check helpers). We only verify that the factory accepts no
    arguments and produces a callable — proving the DI seam doesn't
    accidentally require explicit arguments.
    """
    probe = build_capability_probe()
    assert callable(probe)


@pytest.mark.parametrize("placeholder", ["your-api-key-here", "PASTE-YOUR-LLM-KEY-HERE", ""])
def test_default_secrets_check_reports_false_for_placeholder_key(placeholder: str) -> None:
    """#449: a placeholder LLM key → secrets_loaded=false with a var-named detail.

    Drives ``secrets_check_with_placeholder_detection`` with an explicit env
    mapping (F2-clean) so a placeholder string can't masquerade as a loaded
    credential. Sabotage-proof: reverting the placeholder branch flips this
    to ok=True (RED).
    """
    env = {
        _CANON_KEY: placeholder,
        _CANON_ENDPOINT: _REAL_ENDPOINT,
    }
    result = secrets_check_with_placeholder_detection(env)

    assert result.ok is False
    # The detail names the VAR, never the value (F15).
    assert _CANON_KEY in result.detail
    assert "placeholder" in result.detail.lower()


def test_default_secrets_check_reports_true_for_real_key() -> None:
    """A real-looking key + endpoint → secrets_loaded=true (no placeholder match)."""
    env = {
        _CANON_KEY: _REAL_KEY,
        _CANON_ENDPOINT: _REAL_ENDPOINT,
    }
    result = secrets_check_with_placeholder_detection(env)

    assert result.ok is True
    # F15: the real key value never appears in the detail.
    assert _REAL_KEY not in result.detail


def test_probe_reports_placeholder_through_default_secrets_check() -> None:
    """End-to-end: the production probe surfaces secrets_loaded=false for a placeholder.

    Injects the placeholder-env-bound secrets check (a closure over an
    explicit env mapping) so no os.environ mutation is needed.
    """
    env = {_CANON_KEY: "your-api-key-here", _CANON_ENDPOINT: _REAL_ENDPOINT}
    probe = build_capability_probe(
        secrets_check=lambda: secrets_check_with_placeholder_detection(env),
        vector_check=lambda: _ok(),
    )
    result = probe()

    assert result["secrets_loaded"] is False
    assert _CANON_KEY in result["detail"]["secrets_loaded"]


def test_default_check_helpers_return_check_result_shape() -> None:
    """Invoking the probe with NO injected callables exercises the production defaults.

    The two default helpers (``_default_secrets_check`` and
    ``_default_vector_check``) lazy-import ``onboard.check`` on first
    call. Both onboard helpers wrap their work in try/except and return
    a CheckResult either way, so the probe is safe to invoke even in a
    barebones test environment without LLM credentials or a usearch
    index. This test pins that contract: the default code path completes
    and produces the documented dict shape with bool capability flags.
    """
    probe = build_capability_probe()
    result = probe()

    # Shape contract — every key the /healthz/ready endpoint relies on.
    assert set(result.keys()) == {
        "secrets_loaded",
        "vector_search_capable",
        "bm25_search_capable",
        "detail",
    }, f"unexpected probe keys: {sorted(result.keys())}"
    assert isinstance(result["secrets_loaded"], bool)
    assert isinstance(result["vector_search_capable"], bool)
    # BM25 is implicit / always True.
    assert result["bm25_search_capable"] is True
    assert isinstance(result["detail"], dict)
