"""Per-method failure-injection contract for the Linear connector (F68-style).

The F43 behavioural-parity body lives in
:mod:`tests.contracts.test_linear_protocol` (one parametrized assertion
over real + fake). This file is the companion failure-behaviour
coverage: each public ``SourceConnector`` method's failure surface is
driven explicitly (the api client raising, or an un-drained cache) and
the observable outcome asserted — not just shape compliance.

``LinearConnector`` is a concrete class, not a Protocol, so F68's
repo-wide Protocol scan imposes no gate obligation here; this coverage
ships per spec §11 (the connector's failure modes / resilience contract)
so a regression that swallows a transport error or silently returns
empty fails loudly.

Real-impl path is driven against scripted in-memory clients; no real
network call is made.
"""

from __future__ import annotations

from typing import Any

import pytest

from kairix.connectors.linear import LinearConnector, LinearCredentials
from kairix.core.protocols import Container, SourceMetadata
from tests.fakes import FakeLinearApiClient

pytestmark = pytest.mark.contract


def _connector_with_failing_api() -> LinearConnector:
    """Real connector whose api client raises on every paginate/query."""

    class _FailingApi:
        def query(self, document: str, variables: Any) -> dict[str, Any]:
            raise RuntimeError("linear api: simulated query failure")

        def paginate(self, document: str, variables: Any, *, connection: str) -> Any:
            raise RuntimeError("linear api: simulated paginate failure")
            yield  # pragma: no cover — unreachable; marks this a generator

    return LinearConnector(
        credentials=LinearCredentials(api_key="lin_fail_fixture"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: _FailingApi(),  # type: ignore[arg-type, return-value]  # F3-rationale: failing stub satisfies the query/paginate surface the connector calls.
    )


def _connector_empty() -> LinearConnector:
    """Real connector with an empty scripted api (no nodes, cache empty)."""
    api = FakeLinearApiClient(pages={"issues": [[]]})
    return LinearConnector(
        credentials=LinearCredentials(api_key="lin_fixture"),  # pragma: allowlist secret — test fixture
        client_builder=lambda _c: api,
    )


def _workspace_container() -> Container:
    return Container(
        cc_pair_id=1,
        container_id="workspace",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )


def test_list_changes_raises_when_api_paginate_fails() -> None:
    """list_changes propagates the api client's failure (does not swallow it).

    Spec §9: a transport failure surfaces to the orchestrator, which
    dead-letters the tick — the connector must not silently return empty.
    """
    connector = _connector_with_failing_api()
    with pytest.raises(RuntimeError, match="simulated paginate failure"):
        list(connector.list_changes(cursor=None))


def test_retrieve_all_slim_docs_raises_when_api_paginate_fails() -> None:
    """retrieve_all_slim_docs propagates the api client's failure."""
    connector = _connector_with_failing_api()
    with pytest.raises(RuntimeError, match="simulated paginate failure"):
        list(connector.retrieve_all_slim_docs(_workspace_container()))


def test_fetch_raises_keyerror_for_uncached_item() -> None:
    """fetch on an un-drained item raises a fix-pointer KeyError.

    The cache is empty because no list_changes drain populated it — the
    method fails loudly with an actionable message rather than returning
    a silent empty artefact.
    """
    connector = _connector_empty()
    with pytest.raises(KeyError, match="node cache"):
        connector.fetch("issue:UNSEEN")


def test_metadata_for_returns_empty_when_item_unknown() -> None:
    """metadata_for returns an empty SourceMetadata for an unknown id."""
    connector = _connector_empty()
    assert connector.metadata_for("issue:UNSEEN") == SourceMetadata()


def test_source_link_returns_fallback_when_item_unknown() -> None:
    """source_link returns the ``linear://`` fallback for an unknown id."""
    connector = _connector_empty()
    assert connector.source_link("issue:UNSEEN") == "linear://issue:UNSEEN"
