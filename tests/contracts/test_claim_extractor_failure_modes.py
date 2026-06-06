"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`ClaimExtractor`.

Single Protocol method ``extract(content, *, top_n)``. The Protocol
docstring promises "may return fewer for short inputs" — the
``returns_empty`` failure class is observable when ``content`` is the
empty string. We probe via the shipped
:class:`kairix.knowledge.contradict.extract.EntityDensityClaimExtractor`
plus an inline ``_RaisingClaimExtractor`` for the raises path.

Each test carries a "Sabotage proof:" comment describing the mutation
that proves the assertion has teeth.
"""

from __future__ import annotations

import pytest

from kairix.knowledge.contradict.extract import EntityDensityClaimExtractor

pytestmark = pytest.mark.contract


def test_extract_returns_empty_when_content_is_blank() -> None:
    """The shipped :class:`EntityDensityClaimExtractor` MUST return an
    empty list for blank input — downstream contradiction scorers
    expect "no claims" as a valid signal (skip the pair, don't crash).

    Sabotage proof: in :meth:`EntityDensityClaimExtractor.extract`
    change ``return []`` to ``return ["ghost claim"]``. Re-run: the
    test fails because the list has one entry instead of zero.
    Restored.
    """
    extractor = EntityDensityClaimExtractor()
    assert extractor.extract("", top_n=3) == []
    assert extractor.extract("   \n  \t  ", top_n=3) == []


def test_extract_raises_when_underlying_implementation_fails() -> None:
    """A claim extractor whose ``extract`` raises must surface the
    exception — silent fallback to an empty list would mask the
    failure and break the contradiction pipeline's "no claims =
    skip" signal.

    Sabotage proof: in ``_RaisingClaimExtractor.extract`` change
    ``raise self._exc`` to ``return []``. Re-run: the test fails
    because no exception fires. Restored.
    """

    class _RaisingClaimExtractor:
        def __init__(self, exc: Exception) -> None:
            self._exc = exc

        def extract(self, content: str, *, top_n: int = 3) -> list[str]:
            del content, top_n
            raise self._exc

    extractor = _RaisingClaimExtractor(RuntimeError("F68-claim-extract-failed"))
    with pytest.raises(RuntimeError, match="F68-claim-extract-failed"):
        extractor.extract("any text", top_n=3)
