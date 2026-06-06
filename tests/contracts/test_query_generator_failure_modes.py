"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`QueryGenerator`.

One method (``generate``). Failure surface:

  * ``returns_empty`` — generator returns 0 queries when none configured
    for the title (LLM produces nothing usable, sanitiser filters
    everything).
  * ``returns_partial`` — generator returns fewer than ``n`` queries
    when the LLM produces fewer (the "0..n" Protocol contract).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.fakes import FakeQueryGenerator

pytestmark = pytest.mark.contract


def test_generate_returns_empty_when_no_queries_configured() -> None:
    """Unknown title yields an empty list — the generator must not
    invent queries when the LLM (or fake) has nothing to surface.

    Sabotage proof: in ``FakeQueryGenerator.generate`` change
    ``self._queries_by_title.get(title, [])`` to
    ``self._queries_by_title.get(title, [_phantom_query()])``. Re-run:
    the ``== []`` assertion fails. Restored.
    """
    gen = FakeQueryGenerator()
    out = gen.generate("unseen.md", "body content", n=3, categories=["semantic"])
    assert out == [], f"unknown title must yield []; got {out!r}"


def test_generate_returns_partial_when_configured_count_below_n() -> None:
    """When the configured queries list is shorter than n, the
    generator returns the available queries — the Protocol contract is
    "0..n", not "exactly n".

    Sabotage proof: in ``FakeQueryGenerator.generate`` change the
    return to ``list(self._queries_by_title.get(title, []))`` (drop the
    ``[:n]`` slice and pad to n with duplicates). Re-run: the
    ``len == 1`` assertion fails because we now over-return. Restored.
    """
    queries = [SimpleNamespace(text="q1", intent="semantic")]
    gen = FakeQueryGenerator(queries_by_title={"deploy.md": queries})
    out = gen.generate("deploy.md", "body content", n=5, categories=["semantic"])
    assert len(out) == 1, f"asked for n=5, only 1 configured; got {len(out)}"
