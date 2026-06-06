"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`DrainGraphRepository`.

Two Protocol members: ``available`` (property) + ``cypher(query, params)``.
The narrow Protocol exists so the curator drain can be tested with a
3-line fake. :class:`tests.fakes.FakeDrainGraphRepository` carries the
canonical failure knobs (``available=False`` to simulate degraded
backend, ``raise_always=True`` to simulate connection drops).

Two failure-class probes:

  * ``unavailable`` — ``available`` returns False; callers (drain
    tick) skip the tick rather than crash.
  * ``raises`` — ``cypher`` raises when the backend rejects the MERGE
    (transient outage, syntax error, transaction abort).

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeDrainGraphRepository

pytestmark = pytest.mark.contract


def test_available_unavailable_when_backend_offline() -> None:
    """``available`` returns ``False`` when the backend is degraded —
    the drain tick reads this BEFORE issuing any cypher and skips
    cleanly.

    Sabotage proof: in :meth:`FakeDrainGraphRepository.available`
    change ``return self._available`` to ``return True``. Re-run: the
    test fails because the property reports True instead of False.
    Restored.
    """
    repo = FakeDrainGraphRepository(available=False)
    assert repo.available is False, "degraded backend must report False"


def test_cypher_raises_when_backend_rejects_query() -> None:
    """``cypher`` MUST propagate exceptions from the underlying driver
    — silent swallow would let the drain's "applied N rows" counter
    lie about what landed.

    Sabotage proof: in :meth:`FakeDrainGraphRepository.cypher` comment
    out the ``raise RuntimeError(...)`` branch. Re-run: the test fails
    because no exception fires and the call returns ``[]``. Restored.
    """
    repo = FakeDrainGraphRepository(raise_always=True)
    with pytest.raises(RuntimeError, match="raise_always set"):
        repo.cypher("MERGE (n:Entity {name: $value}) RETURN n", {"value": "alpha"})
    # And the call IS recorded — proves the failure happened AT the
    # cypher boundary, not before it.
    assert len(repo.cypher_calls) == 1
