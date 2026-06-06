"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SearchLogger`.

Two methods (``log_search``, ``log_query``). Failure surface:

  * ``raises`` — disk-write / serialisation failures surface verbatim
    so the pipeline can decide whether to swallow (best-effort logging)
    or abort. The Protocol does NOT pin "never raise" — implementations
    that promise it (e.g. JsonlSearchLogger with on-disk fallback) own
    the swallowing in their own tests.
  * ``returns_empty`` — log_search / log_query return ``None``; the
    "empty" shape is "no observable side effect when event is empty".
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeSearchLogger

pytestmark = pytest.mark.contract


def test_log_search_raises_propagates_typed_exception() -> None:
    """A search logger configured with ``raises=`` must surface the
    exception — best-effort wrapping is the caller's responsibility, not
    the Protocol's.

    Sabotage proof: in ``FakeSearchLogger._record`` change
    ``raise self._raises`` to ``return``. Re-run: pytest.raises sees
    nothing. Restored.
    """
    logger = FakeSearchLogger(raises=RuntimeError("F68-log-search-raises"))
    with pytest.raises(RuntimeError, match="F68-log-search-raises"):
        logger.log_search({"query_hash": "x", "intent": "semantic"})


def test_log_query_raises_propagates_typed_exception() -> None:
    """log_query failure surfaces verbatim — same contract as log_search.

    Sabotage proof: as for log_search; the shared ``_record`` helper
    means a single mutation breaks both tests.
    """
    logger = FakeSearchLogger(raises=RuntimeError("F68-log-query-raises"))
    with pytest.raises(RuntimeError, match="F68-log-query-raises"):
        logger.log_query({"query": "alpha", "query_hash": "x"})


def test_log_search_returns_empty_when_event_is_empty_dict() -> None:
    """An empty event is recorded as-is (no validation pruning) — the
    Protocol promises capture, not validation. Observable: the event
    list contains exactly one empty-dict entry.

    Sabotage proof: in ``FakeSearchLogger._record`` change
    ``self.events.append(event)`` to skip empty dicts. Re-run: the
    ``== [{}]`` assertion fails (events stays empty). Restored.
    """
    logger = FakeSearchLogger()
    logger.log_search({})
    assert logger.events == [{}]


def test_log_query_returns_empty_when_event_is_empty_dict() -> None:
    """Empty-event shape for log_query mirrors log_search.

    Sabotage proof: change the shared ``_record`` to filter empty
    dicts; this assertion fails. Restored.
    """
    logger = FakeSearchLogger()
    logger.log_query({})
    assert logger.events == [{}]
