"""Result envelope — ADR v2 §7 per-source freshness + included/excluded surface.

Wraps the existing search-pipeline ``SearchOutput`` with topology
diagnostic shape:

* per-source ``last_synced_at`` + ``age_seconds`` + ``state``
  (``fresh`` / ``stale`` / ``access_revoked`` / ``not_yet_synced``)
* ``included_collections`` / ``excluded_collections`` so the caller can
  render "we searched X collections; Y was excluded because Z"

Wave C lands the envelope dataclasses + a constructor; Wave D wires
the search-pipeline surface to emit this shape; Wave E surfaces it via
``kairix search --envelope`` and ``tool_search_envelope`` MCP tool.

The envelope is back-compat with today's CLI/MCP via
:meth:`ResultEnvelope.as_search_output` — callers that don't want the
extra diagnostic surface keep working unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from kairix.core.connectors.scope_profile_resolver import ExcludedCollection
from kairix.core.protocols import F39Tier

SourceFreshnessState = Literal["fresh", "stale", "access_revoked", "not_yet_synced"]


@dataclass(frozen=True)
class SourceFreshness:
    """Per-source freshness signal for one cc_pair / connector / federated member."""

    source: str
    last_synced_at: str | None
    age_seconds: int | None
    state: SourceFreshnessState


@dataclass(frozen=True)
class ResultChunk:
    """Minimal per-result row carried by the envelope.

    Wraps a search-pipeline result with the topology fields callers
    need for filtering / display. Kept narrower than
    ``BudgetedResult`` so the envelope shape is stable across search-
    side refactors.
    """

    chunk_id: str
    text: str
    score: float
    collection: str
    sensitivity: F39Tier
    source_uri: str


@dataclass(frozen=True)
class ResultEnvelope:
    """The full envelope returned by Wave D+ search surfaces.

    ``total_results`` is the count of ``results`` (kept as an explicit
    field so callers don't need to do ``len(envelope.results)`` on every
    log line). ``included_collections`` is the list of collection names
    actually queried; ``excluded_collections`` is the list excluded by
    the scope resolver (with reason + escalation hint per actor).

    Use :meth:`as_search_output` to fall back to a plain results
    sequence — preserves the back-compat contract for callers that
    don't yet consume the envelope shape.
    """

    results: tuple[ResultChunk, ...]
    included_collections: tuple[str, ...]
    excluded_collections: tuple[ExcludedCollection, ...]
    freshness: tuple[SourceFreshness, ...]
    total_results: int

    def as_search_output(self) -> tuple[ResultChunk, ...]:
        """Back-compat helper — return just the results tuple.

        CLI / MCP callers that don't yet consume the envelope shape use
        this to keep their existing code paths unchanged. The envelope
        wraps; it doesn't replace.
        """
        return self.results


def _classify_freshness(
    *,
    age_seconds: int | None,
    fresh_threshold_seconds: int = 3600,
) -> SourceFreshnessState:
    """Bucket ``age_seconds`` into one of the four freshness states.

    * ``None`` → ``not_yet_synced``
    * ``< 0``  → ``not_yet_synced`` (defensive — a future timestamp
      means the source never ran a real sync)
    * ``<= fresh_threshold_seconds`` → ``fresh``
    * otherwise → ``stale`` (we never auto-bucket to ``access_revoked``;
      that requires an explicit Container.access_state read upstream)
    """
    if age_seconds is None or age_seconds < 0:
        return "not_yet_synced"
    if age_seconds <= fresh_threshold_seconds:
        return "fresh"
    return "stale"


def build_freshness(
    *,
    source: str,
    last_synced_at: str | None,
    age_seconds: int | None,
    container_access_state: str | None = None,
) -> SourceFreshness:
    """Compose a :class:`SourceFreshness` row from raw measurements.

    Surfaces ``access_revoked`` when ``container_access_state ==
    'REVOKED'`` so the envelope distinguishes "stale because we haven't
    synced" from "stale because we can't sync".
    """
    if container_access_state == "REVOKED":
        state: SourceFreshnessState = "access_revoked"
    else:
        state = _classify_freshness(age_seconds=age_seconds)
    return SourceFreshness(
        source=source,
        last_synced_at=last_synced_at,
        age_seconds=age_seconds,
        state=state,
    )


def build_envelope(
    *,
    results: Sequence[ResultChunk],
    included_collections: Sequence[str],
    excluded_collections: Sequence[ExcludedCollection] = (),
    freshness: Sequence[SourceFreshness] = (),
) -> ResultEnvelope:
    """Compose a :class:`ResultEnvelope` with derived ``total_results``."""
    return ResultEnvelope(
        results=tuple(results),
        included_collections=tuple(included_collections),
        excluded_collections=tuple(excluded_collections),
        freshness=tuple(freshness),
        total_results=len(results),
    )
