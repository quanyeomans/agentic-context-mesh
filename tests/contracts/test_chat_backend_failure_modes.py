"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ChatBackend`.

``ChatBackend.complete`` wraps an LLM chat-completion call. The
Protocol docstring pins the contract: "Raise on credential failure
rather than returning empty content" — silent fallback to empty is
the anti-pattern this Protocol replaces.

:class:`tests.fakes.FakeChatBackend` supports two failure shapes:
``raise_on_call=`` to surface any exception, and exhausting the
scripted ``responses=`` queue to raise :class:`IndexError`. Both probe
the ``raises`` failure class.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeChatBackend

pytestmark = pytest.mark.contract


def test_complete_raises_propagates_credential_error() -> None:
    """A ``raise_on_call=ValueError(...)`` configuration must surface the
    typed exception — proves the "raise on credential failure" Protocol
    contract.

    Sabotage proof: in :meth:`FakeChatBackend.complete` comment out
    ``if self._raise_on_call is not None: raise self._raise_on_call``.
    Re-run: the test fails because the call returns the canned response
    instead of raising. Restored.
    """
    backend = FakeChatBackend(raise_on_call=ValueError("F68-chat-no-credentials"))
    with pytest.raises(ValueError, match="F68-chat-no-credentials"):
        backend.complete(
            prompt="hello",
            api_key="",
            endpoint="https://example.invalid",
            deployment="gpt-fake",
        )


def test_complete_raises_index_error_when_responses_exhausted() -> None:
    """When the scripted ``responses=[]`` queue is empty, the fake MUST
    raise :class:`IndexError` rather than return empty content — the
    docstring explicitly flags silent looping / empty-return as the
    smell this Protocol replaces.

    Sabotage proof: in :meth:`FakeChatBackend.complete` replace the
    ``raise IndexError(...)`` branch with ``return ""``. Re-run: the
    test fails because the call returns ``""`` instead of raising.
    Restored.
    """
    backend = FakeChatBackend(responses=[])  # explicit empty queue
    with pytest.raises(IndexError, match="ran out of canned responses"):
        backend.complete(
            prompt="hello",
            api_key="k",
            endpoint="https://example.invalid",
            deployment="gpt-fake",
        )
