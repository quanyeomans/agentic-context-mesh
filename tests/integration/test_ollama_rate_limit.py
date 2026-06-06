"""F64 rate-limit contract for the ollama provider.

Ollama runs as a local sidecar (typically ``http://localhost:11434``)
and does not enforce per-tenant rate limits. The wire surface has no
``Retry-After`` semantics — the only throttle-like signal an operator
ever sees is a 503 if the daemon is overloaded.

This module pins the F64-required contract: when the transport returns
a throttle-shaped response (429 / 503 with or without ``Retry-After``),
the provider degrades safely — it raises a canonical typed error
rather than:

  * silently retrying forever (would block the embed worker tick), or
  * silently returning an empty / partial vector (would corrupt the
    vec_index), or
  * silently dead-lettering every in-flight item (the SharePoint v2026.5.28
    failure mode that motivated F64).

Sabotage-proof: replace ``raise _map_transport_error(...)`` in
``OllamaProvider.embed_batch`` with ``return [[]]*len(texts)`` →
test_429_raises_client_error_not_silent fails because the call returns
a list of empties instead of raising.

F-rule discipline:
  - F1: no @patch — raising fakes are passed as constructor args.
  - F8: ``pytestmark = pytest.mark.integration``.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.providers import ClientError, UpstreamError
from kairix.providers.ollama import OllamaProvider

pytestmark = pytest.mark.integration


class _ThrottleStubError(Exception):
    """Stand-in for the production ``_HttpStatusError`` carrying a status code.

    Mirrors the same shape ``test_ollama_e2e._HttpStatusStubError`` uses;
    duplicated here so this module is self-contained for F64 audit.
    """

    def __init__(self, status_code: int, retry_after: str | None = None) -> None:
        self.status_code = status_code
        if retry_after is not None:
            # httpx response errors carry headers via .response.headers; mirror
            # that shape so any future Retry-After-aware retry policy can read it.
            self.response = type("R", (), {"status_code": status_code, "headers": {"Retry-After": retry_after}})()
        super().__init__(f"upstream HTTP {status_code}")


class _RaisingTransport:
    """Transport stub whose ``post`` always raises ``err``.

    Mirrors ``kairix.providers.ollama.OllamaTransport`` Protocol shape.
    """

    def __init__(self, err: BaseException) -> None:
        self._err = err
        self.call_count = 0

    def post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        del path, json
        self.call_count += 1
        raise self._err


def _credentials(model: str = "nomic-embed-text") -> Credentials:
    return Credentials(api_key="", endpoint="http://localhost:11434", model=model, dims=0)


# ---------------------------------------------------------------------------
# 429 — throttled request
# ---------------------------------------------------------------------------


def test_429_raises_client_error_not_silent() -> None:
    """A 429 from Ollama maps to a 4xx ClientError and is raised, not
    silently swallowed. The embed worker tick observes the failure
    explicitly and the orchestrator's retry policy (one level up) gets
    to decide whether to back off or dead-letter.

    Sabotage-proof: change ``_map_transport_error`` to return None for
    429 status → embed_batch would yield empty vectors silently and
    this test fails at the ``with pytest.raises(ClientError)`` line.
    """
    transport = _RaisingTransport(_ThrottleStubError(status_code=429, retry_after="2"))
    provider = OllamaProvider(credentials=_credentials(), transport_client=transport)

    with pytest.raises(ClientError) as exc_info:
        provider.embed_batch(["throttled text"])

    assert exc_info.value.status == 429
    assert transport.call_count == 1, (
        "embed_batch should not retry the throttled call inside the provider — "
        "retry policy is the orchestrator's responsibility"
    )


def test_429_without_retry_after_still_raises_client_error() -> None:
    """A 429 with no Retry-After header still maps to ClientError(429).
    The provider does not crash on the missing header (would be a
    KeyError in a less-defensive impl).

    Sabotage-proof: have ``_status_code_of`` dereference response.headers
    unconditionally → this test crashes with AttributeError instead of
    surfacing ClientError.
    """
    transport = _RaisingTransport(_ThrottleStubError(status_code=429, retry_after=None))
    provider = OllamaProvider(credentials=_credentials(), transport_client=transport)

    with pytest.raises(ClientError) as exc_info:
        provider.embed_batch(["throttled text"])

    assert exc_info.value.status == 429


# ---------------------------------------------------------------------------
# 503 — upstream overloaded (the throttle-adjacent status Ollama actually emits)
# ---------------------------------------------------------------------------


def test_503_maps_to_upstream_error_with_status() -> None:
    """A 503 from Ollama maps to UpstreamError carrying ``status_code=503``
    so the orchestrator's retry policy distinguishes upstream-temporary
    from client-permanent.

    Sabotage-proof: change the 5xx branch in ``_map_transport_error`` to
    return ClientError → this test fails on the ``isinstance`` check
    (UpstreamError is not a ClientError subclass).
    """
    transport = _RaisingTransport(_ThrottleStubError(status_code=503))
    provider = OllamaProvider(credentials=_credentials(), transport_client=transport)

    with pytest.raises(UpstreamError) as exc_info:
        provider.embed_batch(["overloaded text"])

    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Provider does NOT retry inside embed_batch (single call to transport)
# ---------------------------------------------------------------------------


def test_429_does_not_consume_remaining_texts() -> None:
    """When the first text in a batch hits 429, embed_batch raises
    immediately — it does NOT continue trying the remaining texts.
    This keeps the failure mode "fail fast + propagate to orchestrator
    retry policy" rather than "silently emit partial vectors".

    Sabotage-proof: wrap the embed loop body in ``try/except: continue``
    → the test fails because call_count would equal len(texts) instead
    of 1, and embed_batch would return partial empty vectors instead of
    raising.
    """
    transport = _RaisingTransport(_ThrottleStubError(status_code=429))
    provider = OllamaProvider(credentials=_credentials(), transport_client=transport)

    with pytest.raises(ClientError):
        provider.embed_batch(["alpha", "beta", "gamma"])

    assert transport.call_count == 1, (
        f"embed_batch should fail-fast on first 429, called transport {transport.call_count} times"
    )
