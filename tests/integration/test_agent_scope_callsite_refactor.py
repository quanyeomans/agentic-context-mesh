"""Integration tests for PR 1.2 — the four production callsites that
previously hardcoded ``<root>/<agent>/memory`` now resolve via
:func:`kairix.core.agents.scope.get_agent_scope`.

Each test drives the public surface (the brief source fetcher, the
temporal-index discovery helper, or the classify router) with an
:class:`AgentScope` injected via the ``config=`` test seam and asserts
that the resulting behaviour reflects every configured surface — not
just a single hardcoded ``/memory`` subdir.

F46 / F47-clean — tests compose via use-case-equivalent surfaces (the
public functions take explicit dependency seams) and never monkeypatch
internals. ``get_agent_scope(config=...)`` is the documented test seam.

Sabotage-proof procedure (executed per assertion): mutate the callsite
back to a hardcoded ``<root>/<agent>/memory`` resolution → confirm
the corresponding assertion fails → restore.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# fetch_memory_logs / fetch_recent_memory — multi-surface read
# ---------------------------------------------------------------------------


def _seed_log(memory_dir: Path, day: date, content: str) -> Path:
    """Helper — write a YYYY-MM-DD.md log file with given content."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    log_path = memory_dir / f"{day.isoformat()}.md"
    log_path.write_text(content, encoding="utf-8")
    return log_path


# Sabotage-proof (executed): reverted fetch_memory_logs to call
# ``agent_memory_path(agent)`` against a hardcoded single root → only
# the first surface's content appeared in the result; assertion that
# both surfaces' marker strings are present failed; restored.
def test_fetch_memory_logs_reads_every_surface_in_scope(tmp_path: Path) -> None:
    """``fetch_memory_logs`` must iterate every surface returned by
    :meth:`AgentScope.memory_paths` — multi-surface agents must not drop
    content from any of their configured memory locations.
    """
    from kairix.agents.briefing.sources import fetch_memory_logs

    surface_a = tmp_path / "vault" / "memory" / "agent-alpha"
    surface_b = tmp_path / "workspaces" / "agent-alpha"
    today = date.today()
    _seed_log(surface_a, today, "## Session A\n[pending] alpha-marker-string\n")
    _seed_log(surface_b, today, "## Session B\n[pending] beta-marker-string\n")

    result = fetch_memory_logs("agent-alpha", memory_dirs=[surface_a, surface_b])

    assert "alpha-marker-string" in result, (
        f"surface A's content missing from result — multi-surface read broken.\n{result!r}"
    )
    assert "beta-marker-string" in result, (
        f"surface B's content missing from result — multi-surface read broken.\n{result!r}"
    )


# Sabotage-proof (executed): reverted fetch_recent_memory to use a
# single hardcoded root → only one surface's section was returned; the
# assertion for both surface markers failed; restored.
def test_fetch_recent_memory_reads_every_surface_in_scope(tmp_path: Path) -> None:
    """``fetch_recent_memory`` (today + yesterday) reads every surface in scope."""
    from kairix.agents.briefing.sources import fetch_recent_memory

    surface_a = tmp_path / "vault" / "memory" / "agent-alpha"
    surface_b = tmp_path / "workspaces" / "agent-alpha"
    today = date.today()
    _seed_log(surface_a, today, "alpha-today-marker")
    _seed_log(surface_b, today, "beta-today-marker")

    result = fetch_recent_memory("agent-alpha", memory_dirs=[surface_a, surface_b])

    assert "alpha-today-marker" in result
    assert "beta-today-marker" in result


# Sabotage-proof (executed): hardwired fetch_memory_logs to read only the
# first element of memory_dirs → the second surface's content vanished;
# assertion failed; restored.
def test_fetch_memory_logs_single_surface_still_works(tmp_path: Path) -> None:
    """Single-surface agents (the common case — most production agents
    only declare a memory surface) behave identically to the legacy
    helper. Regression net against an over-eager refactor that breaks
    the simple shape.
    """
    from kairix.agents.briefing.sources import fetch_memory_logs

    surface = tmp_path / "vault" / "agent-alpha"
    today = date.today()
    yesterday = today - timedelta(days=1)
    _seed_log(surface, today, "[pending] today-item-string\n")
    _seed_log(surface, yesterday, "[blocked] yesterday-item-string\n")

    result = fetch_memory_logs("agent-alpha", memory_dirs=[surface])

    assert "today-item-string" in result
    assert "yesterday-item-string" in result


# ---------------------------------------------------------------------------
# temporal/index — agent memory directory iteration
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): reverted ``get_memory_log_paths`` to walk
# only ``<agent>/memory`` subdirs → the surface-without-/memory log was
# missing from the result list; assertion failed; restored.
def test_get_memory_log_paths_iterates_every_configured_agent_surface(tmp_path: Path) -> None:
    """``get_memory_log_paths`` must yield logs from every configured agent
    scope's surfaces — not only ``<agent>/memory`` subdirectories.

    The production vault shape post-PR-1.2 is flat (logs live directly
    under ``<agent>/`` without a ``/memory`` subdir); the temporal index
    must follow the scope, not the legacy convention.
    """
    from kairix.core.temporal.index import get_memory_log_paths

    # Flat-shape surface for agent-alpha (no /memory subdir)
    flat_dir = tmp_path / "vault" / "agent-alpha"
    today = date.today()
    flat_log = _seed_log(flat_dir, today, "alpha-flat-content")

    config = {
        "agents": {
            "agent-alpha": {
                "surfaces": [{"path": str(flat_dir), "label": "memory"}],
            },
        },
    }

    result = get_memory_log_paths(start=None, end=None, config=config)

    assert str(flat_log) in result, f"flat-shape surface log not discovered: expected {flat_log} in {result}"


# Sabotage-proof (executed): mutated ``get_memory_log_paths`` to iterate
# only the first agent's surfaces → the second agent's log was missing;
# assertion failed; restored.
def test_get_memory_log_paths_iterates_multiple_agents(tmp_path: Path) -> None:
    """Two configured agents → both agents' surfaces contribute logs."""
    from kairix.core.temporal.index import get_memory_log_paths

    alpha_dir = tmp_path / "vault" / "agent-alpha"
    beta_dir = tmp_path / "vault" / "agent-beta"
    today = date.today()
    alpha_log = _seed_log(alpha_dir, today, "alpha-content")
    beta_log = _seed_log(beta_dir, today, "beta-content")

    config = {
        "agents": {
            "agent-alpha": {"surfaces": [{"path": str(alpha_dir), "label": "memory"}]},
            "agent-beta": {"surfaces": [{"path": str(beta_dir), "label": "memory"}]},
        },
    }

    result = get_memory_log_paths(start=None, end=None, config=config)

    assert str(alpha_log) in result
    assert str(beta_log) in result


# ---------------------------------------------------------------------------
# classify/router — episodic write path follows AgentScope.writable_path()
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): reverted ``resolve_target_path`` to the
# pre-PR-1.2 ``f"{ws_root}/{effective_agent}/memory/..."`` formula →
# the resolved path no longer matched the scope's writable_path; the
# assertion fired; restored.
def test_resolve_target_path_episodic_uses_scope_writable_path(tmp_path: Path) -> None:
    """``resolve_target_path(agent, "episodic", date=...)`` must write to
    :meth:`AgentScope.writable_path` (the surface labelled "memory" — or
    the first surface when no label matches) — NOT to a hardcoded
    ``<workspace_root>/<agent>/memory/`` path.

    Uses ``builder`` because the router's legacy ``VALID_AGENTS`` set is
    still hardcoded — relaxing that gate is out of scope for PR 1.2.
    """
    from kairix.core.classify.router import resolve_target_path

    mem_surface = tmp_path / "vault" / "memory" / "builder"
    work_surface = tmp_path / "workspaces" / "builder"
    config = {
        "agents": {
            "builder": {
                "surfaces": [
                    # workspace first to prove writable_path picks the labelled "memory"
                    {"path": str(work_surface), "label": "workspace"},
                    {"path": str(mem_surface), "label": "memory"},
                ],
            },
        },
    }

    out = resolve_target_path(
        agent="builder",
        classification_type="episodic",
        date="2026-06-06",
        config=config,
    )

    assert out == f"{mem_surface}/2026-06-06.md", (
        f"episodic write path should resolve to the agent's memory surface ({mem_surface}/2026-06-06.md), got {out!r}"
    )
    assert "/memory/2026-06-06.md" not in out.replace(str(mem_surface), ""), (
        "episodic path still contains the legacy ``/memory`` subdir suffix"
    )


# Sabotage-proof (executed): reverted router to ignore the "memory"
# label and write to the first surface (the workspace) → assertion that
# the file landed under mem_surface failed; restored.
def test_resolve_target_path_episodic_prefers_memory_label(tmp_path: Path) -> None:
    """Even when a workspace surface is declared first, the episodic write
    path follows the surface labelled ``"memory"`` — that's the
    :meth:`AgentScope.writable_path` contract.
    """
    from kairix.core.classify.router import resolve_target_path

    work_surface = tmp_path / "workspaces" / "builder"
    mem_surface = tmp_path / "vault" / "memory" / "builder"
    config = {
        "agents": {
            "builder": {
                "surfaces": [
                    {"path": str(work_surface), "label": "workspace"},
                    {"path": str(mem_surface), "label": "memory"},
                ],
            },
        },
    }

    out = resolve_target_path(
        agent="builder",
        classification_type="episodic",
        date="2026-06-06",
        config=config,
    )

    assert str(mem_surface) in out, f"episodic path should be under memory surface, got {out!r}"
    assert str(work_surface) not in out, f"episodic path should NOT be under workspace surface, got {out!r}"


# ---------------------------------------------------------------------------
# Fallback synthesis path — operator without an explicit agents block
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): mutated ``get_agent_scope`` to raise
# instead of synthesising → the test failed with ValueError before the
# assertion; restored.
def test_fetch_memory_logs_falls_back_to_synthesised_scope(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """When ``agents.<name>`` is missing but ``agent_defaults`` is set,
    :func:`get_agent_scope` synthesises a scope (``<memory_root>/<agent>``)
    and emits a one-line warning. The brief fetcher must follow that
    synthesised scope so default-config operators still get briefings.
    """
    import logging

    from kairix.agents.briefing.sources import fetch_memory_logs

    memory_root = tmp_path / "defaults" / "memory"
    surface = memory_root / "agent-beta"
    today = date.today()
    _seed_log(surface, today, "[pending] synthesised-scope-marker\n")

    config = {
        "agent_defaults": {
            "memory_root": str(memory_root),
        },
    }

    with caplog.at_level(logging.WARNING, logger="kairix.core.agents.scope"):
        result = fetch_memory_logs("agent-beta", max_tokens=500, config=config)

    assert "synthesised-scope-marker" in result, f"synthesised scope's surface did not contribute content: {result!r}"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("agent-beta" in r.getMessage() for r in warnings), (
        f"expected a synthesis-drift warning naming the agent; got warnings: {[r.getMessage() for r in warnings]}"
    )
