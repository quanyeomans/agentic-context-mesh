"""Contradict use case — single source of truth for ``kairix contradict check``
and ``mcp__contradict``.

Phase 2 of the CLI/MCP feature parity initiative (#168). Pre-Phase-2
drift:

  - CLI accepted ``--top-claims``; MCP did not (hardcoded to 3).
  - CLI default agent was the literal string ``"shared"``; MCP defaulted
    to ``None``. Same query produced different result sets.
  - CLI emitted ``category`` and ``claim`` per result; MCP omitted both.
  - CLI rounded score to 4 decimals in ``--json``; MCP returned raw float.

This use case absorbs every divergence into one ``run_contradict``
returning a ``ContradictOutput`` dataclass. Adapters serialise from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from kairix.core.protocols import SourceRef
from kairix.core.search.scope import Scope

if TYPE_CHECKING:
    from kairix.knowledge.contradict.detector import ContradictionReport

logger = logging.getLogger(__name__)

# PLA-274 breadcrumb keys — read on the detector-result projector
# (``_project``), the envelope writer, AND the envelope reader
# (``from_envelope``); F17 extracts the ≥10-char keys to one edit site.
_KEY_SOURCE_URI = "source_uri"
_KEY_COLLECTION = "collection"
_KEY_SOURCE_PAGE = "source_page"
_KEY_OUTCOME = "outcome"


class ContradictionOutcome(str, Enum):
    """The tri-state verdict a contradiction check returns (#468).

    The single ``has_contradictions`` boolean conflated three distinct
    situations: the store actively disagreeing, the store being silent on a
    claim it holds related material for, and the store holding nothing
    relevant at all. Those are now separated so an agent (or an automated
    gate) never reads 'contradiction' off the mere *absence* of supporting
    evidence.

    - ``CONTRADICTION`` — the store holds evidence that directly conflicts
      with the claim (at least one hit scored at or above the threshold).
    - ``UNSUPPORTED`` — the store holds related content, but none of it
      supports or refutes the claim (candidates were retrieved and scored,
      none rose to a contradiction).
    - ``NOT_FOUND`` — the store holds nothing relevant to the claim (the
      search surfaced no candidates at all).

    A ``str`` Enum so the value serialises directly into the JSON envelope.
    """

    CONTRADICTION = "contradiction"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"


def _default_check_contradiction(**kwargs: Any) -> ContradictionReport:
    from kairix.knowledge.contradict.detector import check_contradiction

    return check_contradiction(**kwargs)


def _default_llm_backend() -> Any:
    from kairix.platform.llm import get_default_backend

    return get_default_backend()


@dataclass(frozen=True)
class ContradictionHit:
    """A single contradicting document, projected from the detector's result.

    PLA-274 — ``title`` / ``collection`` / ``source_page`` / ``source_uri``
    were dropped pre-fix (the surface cited a bare ``path``); they are now
    carried so an agent can cite the exact contradicting source + page via
    :meth:`source_ref`.
    """

    path: str
    score: float
    reason: str
    snippet: str
    category: str = ""
    claim: str = ""
    title: str = ""
    collection: str = ""
    source_page: int | None = None
    source_uri: str = ""
    locator: str | None = None

    def source_ref(self) -> SourceRef:
        """Return the shared :class:`SourceRef` breadcrumb for this hit (F97)."""
        return SourceRef.of(
            path=self.path,
            source_uri=self.source_uri,
            title=self.title or None,
            collection=self.collection or None,
            source_page=self.source_page,
            locator=self.locator,
        )


@dataclass(frozen=True)
class ContradictOutput:
    """Outcome of one ``run_contradict`` invocation.

    Attributes:
        content: The caller's content, unchanged.
        contradictions: Up to ``top_k * top_claims`` ``ContradictionHit``s
            that scored above ``threshold``, best-first.
        outcome: The tri-state verdict (#468). ``CONTRADICTION`` when the
            store actively disagrees, ``UNSUPPORTED`` when it holds related
            but non-probative content, ``NOT_FOUND`` when it holds nothing
            relevant. Defaults to ``NOT_FOUND`` (the safe 'no contradiction
            asserted' verdict used on the empty and error paths).
        has_contradictions: True iff ``outcome is CONTRADICTION`` — i.e.
            only when the ``contradictions`` list is non-empty. It never
            fires on the mere absence of support (that is the whole point
            of #468). Kept as a top-level field for ergonomic
            JSON-envelope reads.
        error: Empty string on success; structured ``"<Class>: <msg>"`` on
            top-level failure.
    """

    content: str
    contradictions: list[ContradictionHit] = field(default_factory=list)
    has_contradictions: bool = False
    outcome: ContradictionOutcome = ContradictionOutcome.NOT_FOUND
    error: str = ""

    @classmethod
    def from_envelope(cls, envelope: dict[str, Any]) -> ContradictOutput:
        """Rebuild a ``ContradictOutput`` from the dict ``contradict_output_to_envelope`` emits.

        The seam for warm-MCP text-mode routing (#421 PR 2.6). The CLI
        dispatcher receives a JSON envelope from the MCP worker; this
        adapter projects it back to the dataclass shape ``format_text``
        already consumes, so the in-process and warm paths render
        byte-identical text.

        ``contradictions`` is the only non-trivial field — each hit dict
        is projected back to a ``ContradictionHit`` with ``str`` /
        ``float`` coercion to defend against accidental JSON-number
        widening (e.g. an int score round-tripped from a sparse
        envelope). Missing keys default to the same values
        ``ContradictionHit`` uses.
        """
        raw_hits = envelope.get("contradictions") or []
        hits = [
            ContradictionHit(
                path=str(h.get("path", "")),
                score=float(h.get("score", 0.0)),
                reason=str(h.get("reason", "")),
                snippet=str(h.get("snippet", "")),
                category=str(h.get("category", "")),
                claim=str(h.get("claim", "")),
                title=str(h.get("title", "") or ""),
                collection=str(h.get(_KEY_COLLECTION, "") or ""),
                source_page=(int(h[_KEY_SOURCE_PAGE]) if isinstance(h.get(_KEY_SOURCE_PAGE), int) else None),
                source_uri=str(h.get(_KEY_SOURCE_URI, "") or ""),
                locator=(str(h["locator"]) if h.get("locator") else None),
            )
            for h in raw_hits
        ]
        has_contradictions = bool(envelope.get("has_contradictions", False))
        return cls(
            content=str(envelope.get("content", "")),
            contradictions=hits,
            has_contradictions=has_contradictions,
            outcome=_outcome_from_envelope(str(envelope.get(_KEY_OUTCOME, "") or ""), has_contradictions),
            error=str(envelope.get("error", "")),
        )


@dataclass(frozen=True)
class ContradictDeps:
    """Injectable dependencies for ``run_contradict``.

    Mirrors ``WorkerDeps`` (kairix/worker.py): ``check_fn`` is
    non-Optional with a ``default_factory`` returning the production
    helper. ``llm_backend`` is a value (not a callable) — when None
    the run_contradict loop resolves the production backend lazily so
    the LLM stack stays unloaded at import time.
    """

    check_fn: Callable[..., ContradictionReport] = field(default_factory=lambda: _default_check_contradiction)
    llm_backend: Any | None = None


def _classify_outcome(report: ContradictionReport) -> ContradictionOutcome:
    """Map a detector report to the tri-state verdict (#468).

    A non-empty ``hits`` list means the store actively disagrees
    (``CONTRADICTION``). Otherwise, retrieved-but-non-probative candidates
    mean the store is silent on a claim it holds related material for
    (``UNSUPPORTED``), and zero candidates mean the store holds nothing
    relevant (``NOT_FOUND``). This is the one place the absence of support
    is prevented from masquerading as a contradiction.
    """
    if report.hits:
        return ContradictionOutcome.CONTRADICTION
    if report.candidates_considered > 0:
        return ContradictionOutcome.UNSUPPORTED
    return ContradictionOutcome.NOT_FOUND


def _outcome_from_envelope(raw_outcome: str, has_contradictions_fallback: bool) -> ContradictionOutcome:
    """Resolve the tri-state ``outcome`` from a JSON envelope, tolerant of legacy shapes.

    A pre-#468 envelope has no ``outcome`` key (``raw_outcome`` is empty);
    fall back to the parsed ``has_contradictions`` so an old warm-worker
    response still round-trips to a sane verdict (``CONTRADICTION`` when it
    flagged one, ``NOT_FOUND`` otherwise).
    """
    try:
        return ContradictionOutcome(raw_outcome)
    except ValueError:
        return ContradictionOutcome.CONTRADICTION if has_contradictions_fallback else ContradictionOutcome.NOT_FOUND


def _project(r: Any) -> ContradictionHit:
    raw_page = getattr(r, _KEY_SOURCE_PAGE, None)
    return ContradictionHit(
        path=str(getattr(r, "doc_path", "")),
        score=float(getattr(r, "score", 0.0)),
        reason=str(getattr(r, "reason", "")),
        snippet=str(getattr(r, "snippet", "")),
        category=str(getattr(r, "category", "")),
        claim=str(getattr(r, "claim", "")),
        # PLA-274 — carry the breadcrumb off the detector result.
        title=str(getattr(r, "title", "") or ""),
        collection=str(getattr(r, _KEY_COLLECTION, "") or ""),
        source_page=int(raw_page) if isinstance(raw_page, int) else None,
        source_uri=str(getattr(r, _KEY_SOURCE_URI, "") or ""),
    )


def run_contradict(
    content: str,
    *,
    agent: str | None = None,
    scope: Scope = Scope.SHARED_AGENT,
    top_k: int = 5,
    threshold: float = 0.45,
    top_claims: int = 3,
    deps: ContradictDeps | None = None,
) -> ContradictOutput:
    """Run contradiction detection and return a structured result.

    Never raises — failures populate ``ContradictOutput.error``.

    Args:
        content: The new content to check.
        agent: Agent scope for retrieval; passed through unchanged.
        scope: Multi-agent scope.
        top_k: Documents compared per claim.
        threshold: Minimum contradiction score (0.0-1.0).
        top_claims: High-signal claims extracted from ``content``.
        deps: Injectable dependencies; production callers leave None.
    """
    d = deps or ContradictDeps()

    try:
        llm = d.llm_backend if d.llm_backend is not None else _default_llm_backend()

        kwargs: dict[str, Any] = {
            "content": content,
            "llm": llm,
            "top_k": top_k,
            "threshold": threshold,
            "top_claims": top_claims,
            "scope": scope,
        }
        if agent is not None:
            kwargs["agent"] = agent

        report = d.check_fn(**kwargs)
        hits = [_project(r) for r in report.hits]
        outcome = _classify_outcome(report)
        return ContradictOutput(
            content=content,
            contradictions=hits,
            has_contradictions=outcome is ContradictionOutcome.CONTRADICTION,
            outcome=outcome,
        )
    except Exception as exc:
        logger.warning("run_contradict failed: %s", exc, exc_info=True)
        return ContradictOutput(
            content=content,
            contradictions=[],
            has_contradictions=False,
            outcome=ContradictionOutcome.NOT_FOUND,
            error=f"{type(exc).__name__}: {exc}",
        )


def contradict_output_to_envelope(out: ContradictOutput) -> dict[str, Any]:
    """Project a ``ContradictOutput`` to the JSON envelope MCP callers receive."""
    return {
        "content": out.content,
        "contradictions": [
            {
                "path": h.path,
                "score": h.score,
                "reason": h.reason,
                "snippet": h.snippet,
                "category": h.category,
                "claim": h.claim,
                # PLA-274 — full breadcrumb (keys mirror SourceRef) so the
                # contradicting source can be cited + re-opened, not just
                # named by path.
                "title": h.title,
                _KEY_COLLECTION: h.collection,
                _KEY_SOURCE_PAGE: h.source_ref().source_page,
                _KEY_SOURCE_URI: h.source_ref().source_uri,
                "locator": h.source_ref().locator,
            }
            for h in out.contradictions
        ],
        "has_contradictions": out.has_contradictions,
        # #468 — the tri-state verdict rides on the envelope so BOTH the CLI
        # ``--json`` surface and the MCP tool expose the same distinction
        # (contradiction / unsupported / not_found), never just the boolean.
        _KEY_OUTCOME: out.outcome.value,
        "error": out.error,
    }
