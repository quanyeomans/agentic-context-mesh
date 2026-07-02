"""Contract: CLI ↔ MCP behavioural parity for the synthesis domain (PLA-323 / W5b).

W1b (PLA-318) split the MCP tool monolith into per-domain adapters; the
synthesis adapters (``prep`` / ``research`` / ``brief`` / ``contradict``)
now live in ``kairix/agents/mcp/tools/synthesis.py``. W5b finishes the DRY:
each capability has ONE use case (``run_prep`` / ``run_research_use_case`` /
``run_brief`` / ``run_contradict``) plus its ``*_output_to_envelope``
serialiser, and BOTH surfaces — the ``kairix <sub>`` CLI and the
``tool_<name>`` MCP adapter — parse → call that use case → serialise. No
business logic lives in either adapter.

This module is the F43-shaped proof of that convergence: every assertion
runs over the TWO surfaces (the ≥2 "impls") through ONE parametrized body,
so the CLI and the MCP adapter can never drift apart while the suite stays
green. The three co-asserted invariants are:

* **same use case** — both surfaces' source references the same ``run_*``
  call (the CLI's ``main`` and the MCP adapter body).
* **same envelope** — driven with the SAME injected fakes, the CLI ``--json``
  path and the MCP tool emit byte-identical envelope dicts.
* **same breadcrumb** — every agent-facing source row on each surface carries
  a resolvable ``source_uri`` (PLA-274 / F97), and the ``brief`` content on
  each surface carries the deterministic ``## Sources`` footer (PLA-266).

The surfaces are exercised through their public entry points only (CLI
``main(argv, deps=...)`` + MCP ``tool_<name>(..., deps=...)``) with fakes
injected via the existing DI seams — no monkey-patching of kairix internals.
"""

from __future__ import annotations

import inspect
import io
import json
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# CLI entry points (public ``main`` per subcommand).
from kairix.agents.briefing.cli import main as brief_cli_main

# MCP adapters — imported from the OWNED split module so a divergence in
# ``tools/synthesis.py`` is caught here directly (server.py re-exports the
# same function objects).
from kairix.agents.mcp.tools.synthesis import (
    tool_brief,
    tool_contradict,
    tool_prep,
    tool_research,
)
from kairix.agents.prep.cli import main as prep_cli_main
from kairix.agents.research.cli import main as research_cli_main
from kairix.core.health import HealthDeps, reset_health_probe_cache
from kairix.core.protocols import SourceRef
from kairix.knowledge.contradict.cli import main as contradict_cli_main
from kairix.knowledge.contradict.detector import ContradictionReport
from kairix.use_cases.brief import BriefDeps, reset_brief_output_cache
from kairix.use_cases.contradict import ContradictDeps
from kairix.use_cases.prep import PrepDeps, reset_prep_summary_cache
from kairix.use_cases.research import ResearchDeps

pytestmark = pytest.mark.contract

# The deterministic ``## Sources`` citation footer heading ``run_brief``
# renders into a brief's content (PLA-266). Held as a test-local constant —
# reaching into the production module's private ``_SOURCES_HEADING`` would be
# an internal-name import (F5).
_BRIEF_SOURCES_HEADING = "## Sources"

# The two surfaces every synthesis capability is exposed through. They are
# the ≥2 "impls" the parametrized bodies below run each assertion over.
_SURFACES: tuple[str, ...] = ("cli", "mcp")

# A connector-style URI distinct from the on-disk path, so the breadcrumb
# assertions prove a real resolvable pointer rides through — not just a
# path echoed back.
_URI = "m365://sites/acme/notes"


# ---------------------------------------------------------------------------
# Prep fakes — duck-typed search result + a chat that returns a fixed summary.
# ---------------------------------------------------------------------------


class _StubHit:
    """Duck-typed FusedResult — prep's context formatter reads ``title``/``path``."""

    def __init__(self, title: str, path: str) -> None:
        self.title = title
        self.path = path


class _StubBudgeted:
    """Duck-typed BudgetedResult — reads ``result``/``content``."""

    def __init__(self, title: str, path: str, content: str) -> None:
        self.result = _StubHit(title, path)
        self.content = content


class _StubSearchResult:
    """Duck-typed SearchResult — reads ``results``."""

    def __init__(self, hits: list[_StubBudgeted]) -> None:
        self.results = hits


def _prep_deps() -> PrepDeps:
    """A ``PrepDeps`` whose search returns one grounded source and whose chat
    returns a fixed summary. Content ≥40 chars clears prep's snippet floor."""
    sr = _StubSearchResult(
        [
            _StubBudgeted(
                "role-card",
                f"{_URI}/agent-alpha.md",
                "agent-alpha is the VP of People at Acme. Reports to the CEO.",
            )
        ]
    )

    def fake_search(**_kwargs: Any) -> Any:
        return sr

    def fake_chat(**_kwargs: Any) -> str:
        return "agent-alpha is the VP of People at Acme."

    return PrepDeps(search_fn=fake_search, chat_fn=fake_chat)


# ---------------------------------------------------------------------------
# Research fakes — an orchestrator stub returning a fixed synthesis result.
# ---------------------------------------------------------------------------


def _research_deps() -> ResearchDeps:
    def fake_research(**kwargs: Any) -> dict[str, Any]:
        return {
            "query": kwargs.get("query", ""),
            "synthesis": "Composed answer grounded in the retrieved evidence.",
            "retrieved_chunks": [
                {
                    "path": "notes/a.md",
                    "snippet": "supporting evidence",
                    "source_ref": {"source_uri": f"{_URI}/a.md", "path": "notes/a.md", "title": "A"},
                }
            ],
            "gaps": ["what about the edge case?"],
            "confidence": 0.6,
            "turns": 2,
        }

    return ResearchDeps(research_fn=fake_research)


# ---------------------------------------------------------------------------
# Contradict fakes — a detector stub returning one contradicting hit.
# ---------------------------------------------------------------------------


@dataclass
class _FakeContradictionResult:
    doc_path: str = "docs/old.md"
    score: float = 0.78
    reason: str = "contradicts the new claim"
    snippet: str = "The system uses option A."
    category: str = "status_mismatch"
    claim: str = "The system now uses option B."
    title: str = "Old Doc"
    collection: str = "reference-library"
    source_page: int = 3
    source_uri: str = f"{_URI}/old.md"


class _FakeLLM:
    def chat(self, _messages: list[dict[str, Any]]) -> str:
        return "{}"


def _contradict_deps() -> ContradictDeps:
    def fake_check(**_kwargs: Any) -> ContradictionReport:
        return ContradictionReport.of([_FakeContradictionResult()])

    return ContradictDeps(check_fn=fake_check, llm_backend=_FakeLLM())


# ---------------------------------------------------------------------------
# Brief fakes — a healthy probe + a config-resolvable agent + fixed sources.
# ---------------------------------------------------------------------------


def _healthy_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


def _brief_config() -> dict[str, object]:
    """Config declaring ``agent-alpha`` with one surface so it resolves (PLA-265)."""
    return {"agents": {"agent-alpha": {"surfaces": [{"path": "memory/agent-alpha", "label": "memory"}]}}}


def _brief_deps() -> BriefDeps:
    return BriefDeps(
        generate_fn=lambda _agent, **_: "Briefing body line 1\nBriefing body line 2",
        briefing_dir_fn=lambda: Path("/var/kairix"),
        config_fn=_brief_config,
        sources_fn=lambda _agent: [
            SourceRef.of(
                path="notes/brief-src.md",
                source_uri=f"{_URI}/brief-src.md",
                title="Brief Source",
                collection="reference-library",
            )
        ],
        health_deps=_healthy_health_deps(),
    )


def _reset_synthesis_caches() -> None:
    """Clear every process-shared cache the synthesis use cases touch so each
    surface invocation recomputes from its injected fakes (no cross-surface
    or cross-test cache bleed can fake parity)."""
    reset_prep_summary_cache()
    reset_brief_output_cache()
    reset_health_probe_cache()


# ---------------------------------------------------------------------------
# Capability table — one row per synthesis capability, describing both surfaces.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Capability:
    name: str
    run_token: str  # the ``run_*`` call both surfaces must reference
    use_case_import: str  # the ``from kairix.use_cases.<x> import`` the MCP body must carry
    cli_main: Callable[..., Any]
    cli_argv: list[str]
    mcp_tool: Callable[..., Any]
    mcp_kwargs: dict[str, Any]
    make_deps: Callable[[], Any]
    # Extract the agent-facing source-breadcrumb rows (each must carry a
    # resolvable ``source_uri``) from an envelope dict.
    source_rows: Callable[[dict[str, Any]], list[dict[str, Any]]]
    # ``## Sources`` footer marker expected in the envelope's ``content``
    # field, or None when the capability has no rendered content footer.
    footer_marker: str | None = None
    footer_field: str = "content"


_QUERY = "what is agent-alpha's role?"
_CONTENT = "The system now uses option B for everything."

_CAPABILITIES: tuple[_Capability, ...] = (
    _Capability(
        name="prep",
        run_token="run_prep(",
        use_case_import="from kairix.use_cases.prep import",
        cli_main=prep_cli_main,
        cli_argv=[_QUERY, "--json"],
        mcp_tool=tool_prep,
        mcp_kwargs={"query": _QUERY},
        make_deps=_prep_deps,
        source_rows=lambda env: list(env["sources"]),
    ),
    _Capability(
        name="research",
        run_token="run_research_use_case(",
        use_case_import="from kairix.use_cases.research import",
        cli_main=research_cli_main,
        cli_argv=[_QUERY, "--json"],
        mcp_tool=tool_research,
        mcp_kwargs={"query": _QUERY},
        make_deps=_research_deps,
        source_rows=lambda env: [c["source_ref"] for c in env["retrieved_chunks"]],
    ),
    _Capability(
        name="brief",
        run_token="run_brief(",
        use_case_import="from kairix.use_cases.brief import",
        cli_main=brief_cli_main,
        cli_argv=["agent-alpha", "--json"],
        mcp_tool=tool_brief,
        mcp_kwargs={"agent": "agent-alpha"},
        make_deps=_brief_deps,
        source_rows=lambda env: list(env["sources"]),
        footer_marker=_BRIEF_SOURCES_HEADING,
    ),
    _Capability(
        name="contradict",
        run_token="run_contradict(",
        use_case_import="from kairix.use_cases.contradict import",
        cli_main=contradict_cli_main,
        cli_argv=["check", _CONTENT, "--json"],
        mcp_tool=tool_contradict,
        mcp_kwargs={"content": _CONTENT},
        make_deps=_contradict_deps,
        source_rows=lambda env: list(env["contradictions"]),
    ),
)

_CAP_IDS = [c.name for c in _CAPABILITIES]


def _invoke(capability: _Capability, surface: str) -> dict[str, Any]:
    """Drive one synthesis capability through one surface and return its
    envelope dict. Resets the shared caches first + builds fresh deps so the
    two surfaces are compared on equal footing, never through a warm cache."""
    _reset_synthesis_caches()
    deps = capability.make_deps()
    if surface == "mcp":
        result = capability.mcp_tool(**capability.mcp_kwargs, deps=deps)
        assert isinstance(result, dict), f"{capability.name} MCP tool returned {type(result)!r}, expected dict"
        return result
    # CLI ``--json`` path — capture stdout (the envelope), swallow the
    # subprocess-narration stderr, and tolerate the SystemExit some CLI
    # ``main`` functions raise as their exit signal.
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            capability.cli_main(capability.cli_argv, deps=deps)
    except SystemExit:
        pass
    stdout = out_buf.getvalue()
    assert stdout.strip(), f"{capability.name} CLI --json produced no stdout; stderr:\n{err_buf.getvalue()}"
    envelope: dict[str, Any] = json.loads(stdout)
    return envelope


# ---------------------------------------------------------------------------
# Parity bodies — ONE assertion, run over BOTH surfaces (the ≥2 impls).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("capability", _CAPABILITIES, ids=_CAP_IDS)
def test_both_surfaces_route_through_the_same_use_case(capability: _Capability) -> None:
    """Both the CLI ``main`` and the MCP adapter reference the SAME ``run_*``
    use case — neither surface re-implements the domain logic inline.

    Sabotage proof (executed 2026-07-02): rewrite ``tools/synthesis.py::
    tool_brief`` to build the envelope inline instead of calling
    ``run_brief`` — this body fails on the MCP ``run_token`` assertion for
    the ``brief`` case. Restored.
    """
    cli_src = inspect.getsource(capability.cli_main)
    mcp_src = inspect.getsource(capability.mcp_tool)
    assert capability.run_token in cli_src, (
        f"{capability.name} CLI main must call {capability.run_token} — the shared use case, not inline logic"
    )
    assert capability.run_token in mcp_src, (
        f"{capability.name} MCP adapter must call {capability.run_token} — the shared use case, not inline logic"
    )
    assert capability.use_case_import in mcp_src, (
        f"{capability.name} MCP adapter must import from its use_case module ({capability.use_case_import})"
    )


@pytest.mark.parametrize("capability", _CAPABILITIES, ids=_CAP_IDS)
def test_cli_and_mcp_emit_identical_envelope(capability: _Capability) -> None:
    """Driven with the SAME injected fakes, the CLI ``--json`` path and the
    MCP tool emit byte-identical envelope dicts — the anti-drift lock.

    This is the load-bearing parity assertion: if either adapter applies its
    own post-processing, adds/drops a field, or diverges from the shared
    ``*_output_to_envelope`` serialiser, the dict comparison fails.

    Sabotage proof (executed 2026-07-02): in ``tools/synthesis.py::
    tool_contradict`` mutate the returned envelope
    (``{**contradict_output_to_envelope(out), "has_contradictions": False}``)
    — this body fails on the ``contradict`` case with a dict mismatch.
    Restored.
    """
    cli_env = _invoke(capability, "cli")
    mcp_env = _invoke(capability, "mcp")
    assert cli_env == mcp_env, f"{capability.name}: CLI ↔ MCP envelope divergence\nCLI: {cli_env!r}\nMCP: {mcp_env!r}"


@pytest.mark.parametrize("surface", _SURFACES)
@pytest.mark.parametrize("capability", _CAPABILITIES, ids=_CAP_IDS)
def test_surface_source_rows_carry_resolvable_source_uri(capability: _Capability, surface: str) -> None:
    """Every agent-facing source row each surface returns carries a resolvable
    ``source_uri`` breadcrumb (PLA-274 / F97) — an agent can cite or re-open
    the grounding source from either surface, not just read a bare path.
    """
    envelope = _invoke(capability, surface)
    rows = capability.source_rows(envelope)
    assert rows, f"{capability.name} [{surface}] returned no source rows to prove the breadcrumb on"
    for row in rows:
        assert row.get("source_uri"), (
            f"{capability.name} [{surface}] source row missing a resolvable source_uri: {row!r}"
        )


@pytest.mark.parametrize("surface", _SURFACES)
def test_brief_sources_footer_rendered_on_both_surfaces(surface: str) -> None:
    """The ``brief`` capability renders the deterministic ``## Sources`` footer
    (PLA-266) into its ``content`` on BOTH surfaces — the citation footer is
    produced once in ``run_brief`` and rides through each adapter unchanged.

    Sabotage proof (executed 2026-07-02): stub ``render_sources_footer`` in
    ``kairix/use_cases/brief.py`` to ``return ""`` — this body fails for both
    surfaces because the ``## Sources`` heading disappears from ``content``.
    Restored.
    """
    brief = next(c for c in _CAPABILITIES if c.name == "brief")
    envelope = _invoke(brief, surface)
    assert brief.footer_marker is not None  # narrow for mypy — brief declares a footer
    content = envelope[brief.footer_field]
    assert brief.footer_marker in content, (
        f"brief [{surface}] content missing the {brief.footer_marker!r} footer:\n{content}"
    )
