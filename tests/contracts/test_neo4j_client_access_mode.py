"""Contract: ``Neo4jClient.cypher`` derives the session access mode from the
QUERY, not from the call site.

A query containing a Cypher write clause (MERGE/CREATE/SET/DELETE/REMOVE/FOREACH)
opens a WRITE session; everything else opens a READ session (which routes to read
replicas). This is the single source of truth for read/write routing — callers
never pass a ``write=`` flag, so a write can never be silently routed to a READ
session (which Neo4j rejects with ``Neo.ClientError.Statement.AccessMode`` and
``cypher()`` then swallows = a silent no-op: the drain / summary-projector /
entity-purge regression this design replaces).

F1-clean: the driver is injected through the public ``driver_cls=`` constructor
seam (no @patch / private-attr access on kairix internals).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kairix.knowledge.graph.client import Neo4jClient

pytestmark = pytest.mark.contract


def _access_mode_for(query: str) -> str | None:
    """Run ``cypher(query)`` against a mock driver; return the
    ``default_access_mode`` the client opened the session with."""
    driver = MagicMock()
    driver.verify_connectivity.return_value = None
    driver_cls = MagicMock()
    driver_cls.driver.return_value = driver
    client = Neo4jClient(
        uri="bolt://test:7687",
        user="test",
        password="test",  # pragma: allowlist secret
        driver_cls=driver_cls,
    )
    assert client.available
    # _init_constraints() opened a session at connect time — ignore it.
    driver.session.reset_mock()
    client.cypher(query)
    return driver.session.call_args.kwargs.get("default_access_mode")


@pytest.mark.parametrize(
    "query",
    [
        "MERGE (p:Person {name: $name}) SET p.x = 1 RETURN p.name",
        "CREATE (n:Document {id: $id})",
        "MATCH (n {name: $name}) SET n.summary = $summary RETURN n.name AS name",
        "MATCH (n {id: $id}) DETACH DELETE n",
        "MATCH (n) REMOVE n.stale_flag",
    ],
)
def test_write_queries_open_a_write_session(query: str) -> None:
    assert _access_mode_for(query) == "WRITE", f"write query must open a WRITE session: {query!r}"


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n {name: $name}) RETURN n.name AS name",
        "MATCH (n) WHERE n.vault_path IS NOT NULL RETURN n.name",
        "MATCH (n) RETURN labels(n) AS labels, count(n) AS count",
    ],
)
def test_read_queries_open_a_read_session(query: str) -> None:
    assert _access_mode_for(query) == "READ", f"read query must open a READ session: {query!r}"
