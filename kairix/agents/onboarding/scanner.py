"""Discovery logic for ``kairix onboard scan`` + ``kairix onboard agent``
(PR 1.4 / #420).

:func:`scan_for_agents` walks ``memory_root`` for agent-shaped
subdirectories and runs every registered harness detector against each.
:func:`discover_single_agent` does the same for one named agent.

The two entry points share a single internal proposal helper so the
harness-attribution + confidence rules are evaluated identically by
both surfaces.

Design notes:

* **Stable ordering** — scopes are returned sorted by name so the
  rendered yaml diffs cleanly between runs.
* **Never raises on disk IO** — missing roots, permission errors, and
  non-directory candidates all collapse to "no proposal" rather than
  bubbling up. The CLI / MCP wrappers depend on this so they can
  swallow operator typos into a clean "no agents found" envelope.
* **Confidence** — the operator-facing signal of whether to trust the
  proposal as-is. ``"high"`` when a harness-aware detector matched
  AND .md files exist; ``"medium"`` when only the generic detector
  matched; ``"low"`` when markers exist but no .md content backs them.
* **Detector seam** — every public entry point accepts a ``detectors``
  kwarg so contract + integration tests can pin the surface without
  the production registry leaking in.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kairix.core.agents.detectors import (
    HarnessDetector,
    get_registered_detectors,
)
from kairix.core.agents.scope import AgentSurface

logger = logging.getLogger(__name__)

_GENERIC_HARNESS = "generic"
_HIGH = "high"
_MEDIUM = "medium"
_LOW = "low"


@dataclass(frozen=True)
class ProposedScope:
    """One proposed :class:`~kairix.core.agents.scope.AgentScope`
    discovered by the scanner.

    ``confidence`` is ``"high"`` when at least one harness-aware
    detector matched AND at least one surface carried real .md files;
    ``"medium"`` when only the generic detector matched; ``"low"``
    when surfaces were proposed but no .md files were found (the
    proposal is structurally plausible but operators MUST review).

    Operators use ``confidence`` to decide whether to keep the
    proposal verbatim or edit before pasting into kairix.config.yaml.
    """

    name: str
    surfaces: tuple[AgentSurface, ...]
    harness: str
    confidence: str
    file_count: int
    most_recent_mtime: float | None


def _safe_iterdir(root: Path) -> tuple[Path, ...]:
    """Return ``root``'s direct children as a tuple, or ``()`` on any
    disk IO error. Lets callers iterate without try/except boilerplate.
    """
    try:
        return tuple(root.iterdir())
    except OSError as exc:
        logger.debug("safe_iterdir(%s) failed: %s", root, exc)
        return ()


def _walk_surface(surface: AgentSurface) -> tuple[int, float | None]:
    """Return ``(count, max mtime)`` for one surface's .md files.

    Any disk IO error collapses to ``(0, None)`` — never raises.
    Helper extracted from :func:`_scope_file_stats` so the latter
    stays below the cognitive-complexity ceiling.
    """
    count = 0
    latest: float | None = None
    try:
        for md in surface.path.rglob(surface.glob):
            if not md.is_file():
                continue
            count += 1
            mtime = md.stat().st_mtime
            if latest is None or mtime > latest:
                latest = mtime
    except OSError as exc:
        logger.debug(
            "stat walk on surface %s (glob=%r) failed: %s",
            surface.path,
            surface.glob,
            exc,
        )
    return count, latest


def _scope_file_stats(surfaces: Iterable[AgentSurface]) -> tuple[int, float | None]:
    """Return ``(total .md file count across surfaces, max mtime or None)``.

    Used by both ``scan_for_agents`` and ``discover_single_agent`` so
    the file-count + recency calculation is centralised. Delegates the
    per-surface walk to :func:`_walk_surface`.
    """
    total = 0
    latest: float | None = None
    for surface in surfaces:
        count, surface_latest = _walk_surface(surface)
        total += count
        if surface_latest is not None and (latest is None or surface_latest > latest):
            latest = surface_latest
    return total, latest


def _dedupe_surfaces(surfaces: Iterable[AgentSurface]) -> tuple[AgentSurface, ...]:
    """Drop duplicate-path surfaces while preserving insertion order.

    Several detectors can propose the same memory root (claude-code +
    codex both fire on AGENTS.md). The aggregation loop should not
    surface the same path twice in the rendered yaml.
    """
    seen: set[Path] = set()
    out: list[AgentSurface] = []
    for s in surfaces:
        if s.path in seen:
            continue
        seen.add(s.path)
        out.append(s)
    return tuple(out)


def _classify_confidence(
    *,
    harness: str,
    file_count: int,
) -> str:
    """Map (harness, file_count) → confidence label.

    Generic-only matches are at most ``"medium"`` no matter the file
    count — operators should still review because the generic
    detector's marker set (Board.md, MEMORY.md, …) is intentionally
    narrow and can over-match scratch dirs.
    """
    if file_count == 0:
        return _LOW
    if harness == _GENERIC_HARNESS:
        return _MEDIUM
    return _HIGH


def _augment_with_workspace(
    *,
    surfaces: tuple[AgentSurface, ...],
    workspace_root: Path | None,
    agent_name: str,
) -> tuple[AgentSurface, ...]:
    """Append a workspace surface when ``workspace_root/<agent>/`` exists.

    The detectors already propose a workspace surface from sibling
    ``workspaces/<agent>/`` dirs, but the scanner can also be told
    about a separate cross-tree workspace root (the production layout
    keeps memory + workspace under different roots).
    """
    if workspace_root is None:
        return surfaces
    candidate = workspace_root / agent_name
    if not candidate.is_dir():
        return surfaces
    addition = AgentSurface(path=candidate, glob="**/*.md", label="workspace")
    return _dedupe_surfaces((*surfaces, addition))


def _run_detectors(
    agent_name: str,
    candidate_root: Path,
    detectors: tuple[HarnessDetector, ...],
) -> tuple[str | None, list[AgentSurface]]:
    """Iterate every detector, return (chosen_harness, aggregated surfaces).

    Harness attribution prefers the first non-generic detector that matched.
    """
    chosen_harness: str | None = None
    aggregated: list[AgentSurface] = []
    for detector in detectors:
        proposed = detector.propose_surfaces(agent_name, candidate_root)
        if not proposed:
            continue
        if chosen_harness is None or (chosen_harness == _GENERIC_HARNESS and detector.name != _GENERIC_HARNESS):
            chosen_harness = detector.name
        aggregated.extend(proposed)
    return chosen_harness, aggregated


def _build_md_fallback(candidate_root: Path) -> list[AgentSurface] | None:
    """Return a single low-confidence ``memory`` surface when no detector
    matched but the directory carries ``.md`` content; ``None`` otherwise.
    """
    if not candidate_root.is_dir():
        return None
    if not any(candidate_root.rglob("*.md")):
        return None
    return [AgentSurface(path=candidate_root, glob="**/*.md", label="memory")]


def _propose_for_candidate(
    *,
    agent_name: str,
    candidate_root: Path,
    workspace_root: Path | None,
    detectors: tuple[HarnessDetector, ...],
) -> ProposedScope | None:
    """Run every detector against ``candidate_root`` and assemble one
    :class:`ProposedScope` — or ``None`` when no detector proposed
    anything AND the candidate carries no .md files.

    Harness attribution prefers the first non-generic detector that
    matched; if only generic matched, ``harness == "generic"``. Empty
    proposals fall through to the .md-file fallback so directories
    containing markdown still surface as a "low confidence" entry.
    """
    chosen_harness, aggregated = _run_detectors(agent_name, candidate_root, detectors)

    fallback_used = False
    if not aggregated:
        fallback = _build_md_fallback(candidate_root)
        if fallback is None:
            return None
        aggregated = fallback
        chosen_harness = _GENERIC_HARNESS
        fallback_used = True

    surfaces = _augment_with_workspace(
        surfaces=_dedupe_surfaces(aggregated),
        workspace_root=workspace_root,
        agent_name=agent_name,
    )
    file_count, latest = _scope_file_stats(surfaces)
    confidence = (
        _LOW
        if fallback_used and file_count == 0
        else _classify_confidence(
            harness=chosen_harness or _GENERIC_HARNESS,
            file_count=file_count,
        )
    )
    return ProposedScope(
        name=agent_name,
        surfaces=surfaces,
        harness=chosen_harness or _GENERIC_HARNESS,
        confidence=confidence,
        file_count=file_count,
        most_recent_mtime=latest,
    )


def scan_for_agents(
    *,
    memory_root: Path,
    workspace_root: Path | None = None,
    detectors: tuple[HarnessDetector, ...] | None = None,
) -> tuple[ProposedScope, ...]:
    """Scan ``memory_root`` for agent-shaped subdirs.

    For each subdirectory under ``memory_root`` that carries .md files
    OR matches a harness detector's markers, returns a
    :class:`ProposedScope` carrying the union of surfaces every
    detector proposed.

    ``workspace_root`` adds a ``workspace`` surface for
    ``workspace_root/<name>/`` when that directory exists.

    ``detectors`` defaults to :func:`~kairix.core.agents.detectors.get_registered_detectors`
    — the test seam lets contract + integration tests pin the surface
    without the production registry leaking in.

    Scopes are returned sorted by name. The function never raises —
    disk IO errors collapse to an empty proposal so a typo'd path
    yields ``()`` rather than a crash.
    """
    dets = detectors if detectors is not None else get_registered_detectors()
    proposals: list[ProposedScope] = []
    for candidate in _safe_iterdir(memory_root):
        if not candidate.is_dir():
            continue
        proposal = _propose_for_candidate(
            agent_name=candidate.name,
            candidate_root=candidate,
            workspace_root=workspace_root,
            detectors=dets,
        )
        if proposal is not None:
            proposals.append(proposal)
    return tuple(sorted(proposals, key=lambda p: p.name))


def discover_single_agent(
    agent_name: str,
    *,
    memory_root: Path,
    workspace_root: Path | None = None,
    harness: str | None = None,
    detectors: tuple[HarnessDetector, ...] | None = None,
) -> ProposedScope:
    """Discover surfaces for one named agent.

    When ``harness`` is ``None`` every detector runs; when specified,
    only the detector whose ``name`` matches runs.

    Raises ``ValueError`` when no detector proposes any surface AND
    no .md files exist at ``memory_root / agent_name``. Callers MUST
    get a hard signal so they don't silently write an empty-surfaces
    scope into yaml.
    """
    dets = detectors if detectors is not None else get_registered_detectors()
    if harness is not None:
        dets = tuple(d for d in dets if d.name == harness)
    candidate_root = memory_root / agent_name
    proposal = _propose_for_candidate(
        agent_name=agent_name,
        candidate_root=candidate_root,
        workspace_root=workspace_root,
        detectors=dets,
    )
    if proposal is None:
        raise ValueError(
            f"no detector proposed surfaces for agent {agent_name!r} "
            f"under {memory_root} — confirm the directory exists and "
            f"carries markdown files",
        )
    return proposal
