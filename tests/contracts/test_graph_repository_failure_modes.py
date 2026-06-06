"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`GraphRepository`.

Every public method on :class:`kairix.core.protocols.GraphRepository`
has at least one test here that exercises a named failure class
(``raises`` / ``returns_empty`` / ``unavailable``).

The Neo4j-backed graph is an optional collaborator — every method
must absorb-or-surface its failure cleanly so the SearchPipeline keeps
serving chunk-only results when the graph is down (Bug-class: a
single Neo4j hiccup must not nuke a search response).

Fakes from :mod:`tests.fakes` are used directly — :class:`FakeGraphRepository`
exposes the ``raises=`` + ``available=False`` knobs that drive the
production-equivalent failure shapes.
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import GraphRepository
from tests.fakes import FakeGraphRepository

pytestmark = pytest.mark.contract


def test_available_returns_empty_when_backend_unavailable() -> None:
    """The ``available`` property is the gate every caller checks before
    routing to ``cypher`` / ``find_entity``. The "returns_empty" failure
    class for a bool is ``False`` — and every caller honours it.

    Sabotage proof: change ``FakeGraphRepository.available`` to always
    return True regardless of ``self._available``. Re-ran: this test
    fails because the assertion expects False. Restored.
    """
    repo: GraphRepository = FakeGraphRepository(available=False)
    assert repo.available is False


def test_find_entity_returns_empty_when_name_absent() -> None:
    """Unknown entity-name lookup returns ``None`` (the "returns_empty"
    shape) so callers can distinguish "no entity" from "lookup errored".

    Sabotage proof: change ``FakeGraphRepository.find_entity`` to
    return an empty dict instead of ``None`` for misses. Re-ran:
    the ``is None`` assertion fails. Restored.
    """
    repo: GraphRepository = FakeGraphRepository(entities=[{"name": "agent-alpha"}])
    assert repo.find_entity("agent-zeta") is None
    # Hit path still works — proves the lookup ran (not a global None
    # short-circuit that would mask everything).
    hit = repo.find_entity("agent-alpha")
    assert hit is not None
    assert hit["name"] == "agent-alpha"


def test_entity_in_degrees_returns_empty_when_graph_empty() -> None:
    """An empty graph returns an empty list, not ``None`` — callers
    iterate the result without a null check.

    Sabotage proof: change ``FakeGraphRepository.entity_in_degrees`` to
    return ``None`` on empty graphs. Re-ran: the ``== []`` assertion
    fails (None != []). Restored.
    """
    repo: GraphRepository = FakeGraphRepository(entities=[])
    assert repo.entity_in_degrees() == []


def test_cypher_raises_propagates_typed_exception() -> None:
    """When the Neo4j backend raises (network blip, query error), the
    exception surfaces to the caller — entity-boost callers catch and
    skip, but the cypher Protocol contract is "raise on failure" not
    "silently return []".

    Sabotage proof: change ``FakeGraphRepository.cypher`` to ``return []``
    instead of ``raise self._raises``. Re-ran: ``pytest.raises`` sees
    nothing and the test fails. Restored.
    """
    repo: GraphRepository = FakeGraphRepository(raises=RuntimeError("F68-cypher-raises"))
    with pytest.raises(RuntimeError, match="F68-cypher-raises"):
        repo.cypher("MATCH (n) RETURN n")


def test_cypher_returns_empty_when_no_rows_match() -> None:
    """No-match query returns ``[]`` — distinguishable from the
    ``raises`` shape. Callers can branch on emptiness without a
    try/except.

    Sabotage proof: change ``FakeGraphRepository.cypher`` to
    return ``None`` when no rows. Re-ran: ``== []`` fails. Restored.
    """
    repo: GraphRepository = FakeGraphRepository(entities=[], cypher_rows=[])
    assert repo.cypher("MATCH () RETURN 1") == []
