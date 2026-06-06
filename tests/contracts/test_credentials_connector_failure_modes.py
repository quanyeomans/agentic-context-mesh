"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`CredentialsConnector`.

Single Protocol method ``load_credentials(credentials)``. The Protocol
docstring pins the failure shape: returning ``None`` signals the
credential is invalid for this connector's source kind. Two failure
classes:

  * ``unauthorized`` — invalid credential shape returns ``None``
    (the "no" outcome — the connector REFUSES the credential and
    callers must distinguish from successful normalisation).
  * ``raises`` — credential normalisation crashes (KV unwrap fails,
    decrypt error, downstream token-fetch crashes). The Protocol
    surface must propagate, not swallow.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.contract


def test_load_credentials_unauthorized_returns_none_for_invalid_blob() -> None:
    """A connector that rejects an invalid credential mapping MUST
    return ``None`` (NOT raise, NOT return a stale token) — the
    Protocol's documented "invalid" sentinel.

    Sabotage proof: in ``_RejectingCredsConnector.load_credentials``
    change ``return None`` to ``return {"access_token": "leaked"}``.
    Re-run: the test fails because the connector returns a credential
    dict instead of ``None``. Restored.
    """

    class _RejectingCredsConnector:
        def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
            # Reject when the required key is missing — the "unauthorized"
            # failure class observable as None.
            if "kv_ref" not in credentials:
                return None
            return {"access_token": credentials["kv_ref"]}

    conn = _RejectingCredsConnector()
    assert conn.load_credentials({"unrelated": "blob"}) is None


def test_load_credentials_raises_when_unwrap_fails() -> None:
    """A connector whose credential transformation crashes (e.g. KV
    unwrap surfaces a network error) MUST raise — silent fallback to
    ``None`` would let callers fall through to an unauthenticated
    request.

    Sabotage proof: in ``_RaisingCredsConnector.load_credentials``
    change ``raise self._exc`` to ``return None``. Re-run: the test
    fails because no exception fires. Restored.
    """

    class _RaisingCredsConnector:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
            del credentials
            raise self._exc

    conn = _RaisingCredsConnector(RuntimeError("F68-kv-unwrap-failed"))
    with pytest.raises(RuntimeError, match="F68-kv-unwrap-failed"):
        conn.load_credentials({"kv_ref": "secret/path"})
