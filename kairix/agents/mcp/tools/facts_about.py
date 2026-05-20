"""MCP tool — ``facts_about``: agent-facing fact-store introspection.

Lets an agent ask "what does kairix know about <entity>?" without
running the full SearchPipeline. The tool searches the configured
:class:`FactStore` for hits whose ``record.entity`` matches, returns
the read-surface fields of each hit as a flat dict, and filters out
superseded facts by default so the agent sees the current ground truth.

Dependency injection:

- ``fact_store`` is constructor-injected. Production callers leave it
  ``None`` and the tool resolves a real ``SQLiteFactStore`` against
  the configured ``paths.db_path``. Tests inject ``FakeFactStore``
  pre-loaded with scripted records.

Errors:

- Returns ``{"error": "<Name>", ...}`` rather than raising — agents
  read the ``error`` key to decide whether the call succeeded.
"""

from __future__ import annotations

import logging
from typing import Any

from kairix.core.protocols import FactStore
from kairix.paths import KairixPaths

logger = logging.getLogger(__name__)

__all__ = ["tool_facts_about"]


ERROR_INVALID_INPUT = "InvalidInput"
ERROR_LOOKUP_FAILED = "LookupFailed"


def _hit_to_dict(hit: Any) -> dict[str, Any]:
    """Project a :class:`FactHit` onto the agent-facing read surface.

    Only exposes the Protocol-pinned fields — implementation extras on
    the concrete record (e.g. SQLite rowid) stay internal.
    """
    record = hit.record
    return {
        "entity": record.entity,
        "attribute": record.attribute,
        "value": record.value,
        "confidence": record.confidence,
        "source_turn_ids": list(record.source_turn_ids),
        "extracted_at": record.extracted_at,
        "score": hit.score,
    }


def tool_facts_about(
    entity: str,
    namespace: str | None = None,
    top_k: int = 20,
    *,
    paths: KairixPaths | None = None,
    fact_store: FactStore | None = None,
) -> dict[str, Any]:
    """Return facts about an entity from the configured fact store.

    Parameters
    ----------
    entity:
        The name to search for. Empty string is rejected with the
        ``InvalidInput`` envelope — there is no meaningful "all facts"
        semantics on this surface; agents that want a broad sweep should
        call ``search`` instead.
    namespace:
        Engagement-scope filter. ``None`` (the default) means "across
        all namespaces". Passing a string restricts hits to that
        namespace — used by agents whose session is pinned to a single
        engagement scope.
    top_k:
        Maximum number of hits to return. Default 20 mirrors a typical
        agent introspection budget.
    paths / fact_store:
        Optional DI seams — production callers leave them ``None``.
        Tests inject fakes from ``tests/fakes.py``.

    Returns
    -------
    dict
        Success: ``{"entity", "namespace", "top_k", "hits": [...], "error": ""}``.
        Each hit is a dict with the canonical FactRecord read surface plus
        the recall ``score``. Superseded facts are filtered out by
        ``FactStore.search`` (Protocol default), so the list reflects the
        current ground truth.
        Failure: ``{"error": "<Name>", "detail": "...", "hits": []}``.
    """
    if not entity:
        return {
            "error": ERROR_INVALID_INPUT,
            "detail": "entity was empty; pass a non-empty name",
            "entity": entity,
            "namespace": namespace,
            "top_k": top_k,
            "hits": [],
        }

    if fact_store is None:
        # Defer the heavy SQLite import to call time so cold module
        # imports stay cheap; the production wiring builds an in-process
        # SQLiteFactStore against the configured db path.
        resolved_paths = paths if paths is not None else KairixPaths.resolve()
        from kairix.core.facts import SQLiteFactStore

        fact_store = SQLiteFactStore(db_path=resolved_paths.db_path)

    try:
        hits = fact_store.search(entity, top_k=top_k, namespace=namespace)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("tool_facts_about: fact_store.search raised: %s", exc, exc_info=True)
        return {
            "error": ERROR_LOOKUP_FAILED,
            "detail": f"{type(exc).__name__}: {exc}",
            "entity": entity,
            "namespace": namespace,
            "top_k": top_k,
            "hits": [],
        }

    return {
        "entity": entity,
        "namespace": namespace,
        "top_k": top_k,
        "hits": [_hit_to_dict(h) for h in hits],
        "error": "",
    }
