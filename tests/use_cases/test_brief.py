"""Unit tests for ``kairix.use_cases.brief.run_brief``."""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.core.health import HealthDeps, KairixHealth
from kairix.core.protocols import SourceRef
from kairix.use_cases.brief import (
    BriefDeps,
    BriefOutput,
    brief_output_to_envelope,
    render_sources_footer,
    run_brief,
)


def _healthy_health_deps() -> HealthDeps:
    """Inject probes that report all-green so brief proceeds to generate."""
    return HealthDeps(
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


# Documented role labels the brief used to hardcode. They now resolve via
# the injected ``agents:`` config below, not a module-level allow-list.
_DOCUMENTED_AGENTS = ("builder", "shape", "growth", "consultant")


def _config_defining(*agents: str) -> dict[str, object]:
    """Build a parsed-config dict declaring each agent with one surface.

    This is the F2-clean ``config_fn`` seam: instead of writing a
    ``kairix.config.yaml`` to disk, tests hand ``run_brief`` the parsed
    mapping that AgentScope resolution consumes. A non-empty surface
    means the agent resolves and is briefable.
    """
    return {"agents": {name: {"surfaces": [{"path": f"memory/{name}", "label": "memory"}]} for name in agents}}


def _build_deps(
    *,
    content: str = "",
    raises: bool = False,
    out_dir: Path = Path("/tmp/brief"),
    health_deps: HealthDeps | None = None,
    config: dict[str, object] | None = None,
) -> tuple[BriefDeps, dict[str, list]]:
    captured: dict[str, list] = {"generate": [], "dir": []}
    effective_config = config if config is not None else _config_defining(*_DOCUMENTED_AGENTS)

    def fake_generate(agent: str, **kwargs: object) -> str:
        captured["generate"].append((agent, kwargs))
        if raises:
            raise RuntimeError("boom")
        return content

    def fake_dir() -> Path:
        captured["dir"].append(True)
        return out_dir

    return (
        BriefDeps(
            generate_fn=fake_generate,
            briefing_dir_fn=fake_dir,
            config_fn=lambda: effective_config,
            # Default the PLA-266 structured-citation seam to "no hits" so
            # these tests never fall through to the production hybrid search;
            # the footer + provenance tests below inject their own refs.
            sources_fn=lambda _agent: [],
            health_deps=health_deps or _healthy_health_deps(),
        ),
        captured,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_returns_content_path_and_preview() -> None:
    deps, captured = _build_deps(
        content="\n".join([f"line {i}" for i in range(50)]),
        out_dir=Path("/var/lib/kairix/briefing"),
    )
    out = run_brief("builder", deps=deps)

    assert out.error == ""
    assert out.agent == "builder"
    assert out.content.startswith("line 0")
    assert out.path == "/var/lib/kairix/briefing/builder-latest.md"
    # Preview is first 30 lines, joined by newlines.
    assert out.preview == "\n".join([f"line {i}" for i in range(30)])
    assert captured["generate"][0][0] == "builder"


@pytest.mark.unit
def test_short_content_preview_equals_content() -> None:
    deps, _ = _build_deps(content="line 1\nline 2")
    out = run_brief("shape", deps=deps)
    assert out.preview == "line 1\nline 2"


@pytest.mark.unit
def test_agent_name_lowercased_and_stripped() -> None:
    deps, captured = _build_deps(content="x")
    out = run_brief("  Shape  ", deps=deps)
    assert out.agent == "shape"
    assert captured["generate"][0][0] == "shape"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_agent_with_no_surfaces_returns_error_envelope() -> None:
    """An agent whose config entry resolves to zero surfaces is rejected.

    This is the only InvalidAgent shape that survives PLA-265: a name
    the operator declared but left with no surface to read. The
    generator must never run for it.
    """
    deps, captured = _build_deps(config={"agents": {"ghost": {"surfaces": []}}})
    out = run_brief("ghost", deps=deps)
    assert out.error.startswith("InvalidAgent")
    assert captured["generate"] == []  # never reached the generator


@pytest.mark.unit
def test_config_onboarded_custom_agent_is_accepted() -> None:
    """The PLA-265 fix: an operator-onboarded custom agent name is briefable.

    ``agent-alpha`` is not one of the legacy four names; before the fix
    the hardcoded ``_VALID_AGENTS`` allow-list rejected it even though it
    is declared in ``agents:``. Now its configured surface resolves and
    the brief runs through to the generator.

    Sabotage-proof (executed): re-adding the
    ``if normalised not in {"builder","shape","growth","consultant"}``
    guard makes this test fail — ``agent-alpha`` returns an InvalidAgent
    envelope and the generator is never called.
    """
    deps, captured = _build_deps(content="alpha briefing body", config=_config_defining("agent-alpha"))
    out = run_brief("agent-alpha", deps=deps)
    assert out.error == ""
    assert out.agent == "agent-alpha"
    assert out.content == "alpha briefing body"
    assert captured["generate"][0][0] == "agent-alpha"


@pytest.mark.unit
def test_empty_agent_string_is_invalid() -> None:
    deps, _ = _build_deps()
    out = run_brief("", deps=deps)
    assert "InvalidAgent" in out.error


@pytest.mark.parametrize("agent", ["builder", "shape", "growth", "consultant"])
@pytest.mark.unit
def test_each_documented_agent_is_accepted(agent: str) -> None:
    deps, _ = _build_deps(content="x")
    out = run_brief(agent, deps=deps)
    assert out.error == ""
    assert out.agent == agent


# ---------------------------------------------------------------------------
# Failure path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_failure_yields_error_envelope() -> None:
    deps, _ = _build_deps(raises=True)
    out = run_brief("builder", deps=deps)
    assert out.error.startswith("RuntimeError:")
    assert out.content == ""


# ---------------------------------------------------------------------------
# Envelope projection
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_envelope_carries_all_fields() -> None:
    out = BriefOutput(
        agent="builder",
        content="full content",
        path="/p",
        preview="preview",
        health=KairixHealth(),
    )
    env = brief_output_to_envelope(out)
    assert env["agent"] == "builder"
    assert env["content"] == "full content"
    assert env["path"] == "/p"
    assert env["preview"] == "preview"
    assert env["error"] == ""
    assert env["health"]["vector_search"] == "ok"
    assert env["health"]["next_action"] == ""


@pytest.mark.unit
def test_envelope_carries_error_field() -> None:
    out = BriefOutput(agent="x", error="InvalidAgent: 'x'. Must be ...")
    env = brief_output_to_envelope(out)
    assert env["error"].startswith("InvalidAgent")
    assert env["content"] == ""
    # Even an error envelope carries the health snapshot.
    assert "vector_search" in env["health"]


# ---------------------------------------------------------------------------
# W3: health envelope contract (#246)
# ---------------------------------------------------------------------------


def _chat_offline_health_deps() -> HealthDeps:
    return HealthDeps(
        secrets_loaded_fn=lambda: False,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
        neo4j_available_fn=lambda: True,
    )


@pytest.mark.unit
def test_healthy_state_brief_carries_clean_health_field() -> None:
    deps, _ = _build_deps(content="hello world")
    out = run_brief("builder", deps=deps)
    assert out.health.vector_search == "ok"
    assert out.health.chat == "ok"
    assert out.health.degraded_reason == ""
    assert out.health.next_action == ""


@pytest.mark.unit
def test_chat_offline_returns_empty_content_but_prescriptive_next_action() -> None:
    """W3 contract: when chat is offline brief returns an envelope with
    empty content (not a misleading partial success) and a directive
    that tells the agent to fall back to tool_search.

    Sabotage anchor: dropping the directive in ``brief_next_action``
    makes this test fail on the ``next_action`` assertion."""
    deps, captured = _build_deps(content="never seen", health_deps=_chat_offline_health_deps())
    out = run_brief("builder", deps=deps)

    # The brief was not generated (would have crashed in production).
    assert out.content == ""
    assert out.path == ""
    assert out.preview == ""
    # No exception surfaced; ``error`` stays empty — the affordance is on health.
    assert out.error == ""
    # Health surfaces the degradation.
    assert out.health.chat == "offline"
    assert out.health.degraded_reason != ""
    # Prescriptive directive points the agent at tool_search.
    assert out.health.next_action != ""
    assert "tool_search" in out.health.next_action
    assert "fall back" in out.health.next_action.lower()
    # Sabotage: the generator must not have been called when chat is offline.
    assert captured["generate"] == []


@pytest.mark.unit
def test_brief_envelope_includes_health_dict() -> None:
    out = BriefOutput(agent="builder", content="x", health=KairixHealth())
    env = brief_output_to_envelope(out)
    assert "health" in env
    assert env["health"]["chat"] == "ok"
    assert env["health"]["next_action"] == ""


@pytest.mark.unit
def test_brief_invalid_agent_still_carries_health_snapshot() -> None:
    deps, _ = _build_deps(
        health_deps=_chat_offline_health_deps(),
        config={"agents": {"ghost": {"surfaces": []}}},
    )
    out = run_brief("ghost", deps=deps)
    assert out.error.startswith("InvalidAgent")
    # Even on validation failure the agent gets a health snapshot.
    assert out.health.chat == "offline"
    assert out.health.next_action != ""


# ---------------------------------------------------------------------------
# PLA-266: structured SourceRef citations + ## Sources footer
# ---------------------------------------------------------------------------


def _three_refs() -> list[SourceRef]:
    """Three resolvable breadcrumbs — meets the >=3-citations-per-brief SLO."""
    return [
        SourceRef.of(
            path="archive/handbook.zip#1536",
            source_uri="sharepoint://acme-site/handbook.zip",
            title="Acme Handbook",
            collection="shared",
        ),
        SourceRef.of(path="notes/onboarding.md", title="Onboarding", collection="shared"),
        SourceRef.of(
            path="decisions/2026-06-30.md",
            source_uri="obsidian://decisions/2026-06-30.md",
            title="Deploy decision",
            collection="agent-alpha",
        ),
    ]


def _deps_with_sources(
    *,
    content: str,
    sources: list[SourceRef],
    out_dir: Path = Path("/var/lib/kairix/briefing"),
) -> BriefDeps:
    """A BriefDeps whose generator + structured-source seam are both faked.

    ``sources_fn`` is the PLA-266 injection seam: instead of running a real
    hybrid search, tests hand ``run_brief`` the SourceRefs directly so the
    footer-assembly + envelope-projection are proven without an index.
    """
    return BriefDeps(
        generate_fn=lambda _agent, **_kw: content,
        briefing_dir_fn=lambda: out_dir,
        config_fn=lambda: _config_defining("agent-alpha"),
        sources_fn=lambda _agent: list(sources),
        health_deps=_healthy_health_deps(),
    )


@pytest.mark.unit
def test_output_carries_structured_sourcerefs() -> None:
    """The brief result embeds the retrieved chunks as resolvable SourceRefs
    so an MCP caller can cite or re-open any source (PLA-266).

    Sabotage-proof (executed): dropping ``sources=tuple(sources)`` from the
    ``BriefOutput`` built in ``run_brief`` makes this fail (``out.sources``
    is empty). Restored.
    """
    out = run_brief("agent-alpha", deps=_deps_with_sources(content="body", sources=_three_refs()))

    assert out.error == ""
    assert len(out.sources) == 3
    assert all(isinstance(r, SourceRef) for r in out.sources)
    assert out.sources[0].source_uri == "sharepoint://acme-site/handbook.zip"


@pytest.mark.unit
def test_content_gains_deterministic_sources_footer() -> None:
    """The ## Sources footer is appended to the brief content and renders the
    canonical source_uri of every citation.

    Sabotage-proof (executed): removing the footer append in ``run_brief``
    makes this fail (no ``## Sources`` in content). Restored.
    """
    out = run_brief("agent-alpha", deps=_deps_with_sources(content="briefing body", sources=_three_refs()))

    assert "## Sources" in out.content
    # The body precedes the footer.
    assert out.content.startswith("briefing body")
    # Every citation's canonical breadcrumb is rendered.
    assert "sharepoint://acme-site/handbook.zip" in out.content
    assert "obsidian://decisions/2026-06-30.md" in out.content
    # The passthrough note falls back to its path as the resolvable pointer.
    assert "notes/onboarding.md" in out.content


@pytest.mark.unit
def test_no_sources_appends_no_footer() -> None:
    """With zero retrieved chunks the content is left byte-identical — no
    empty ``## Sources`` heading dangling on the brief.
    """
    out = run_brief("agent-alpha", deps=_deps_with_sources(content="briefing body", sources=[]))

    assert out.content == "briefing body"
    assert "## Sources" not in out.content
    assert out.sources == ()


@pytest.mark.unit
def test_envelope_carries_sources_breadcrumbs() -> None:
    """``brief_output_to_envelope`` projects each SourceRef to its breadcrumb
    dict so MCP callers get machine-parseable provenance (PLA-266).

    Sabotage-proof (executed): dropping the ``"sources"`` key from
    ``brief_output_to_envelope`` makes this fail (KeyError). Restored.
    """
    out = run_brief("agent-alpha", deps=_deps_with_sources(content="body", sources=_three_refs()))
    env = brief_output_to_envelope(out)

    assert len(env["sources"]) == 3
    assert env["sources"][0]["source_uri"] == "sharepoint://acme-site/handbook.zip"
    assert env["sources"][0]["collection"] == "shared"


@pytest.mark.unit
def test_envelope_sources_round_trip_through_from_envelope() -> None:
    """The structured citations survive the warm-MCP envelope round-trip."""
    out = run_brief("agent-alpha", deps=_deps_with_sources(content="body", sources=_three_refs()))
    env = brief_output_to_envelope(out)
    rebuilt = BriefOutput.from_envelope(env)

    assert len(rebuilt.sources) == 3
    assert rebuilt.sources[0].source_uri == "sharepoint://acme-site/handbook.zip"
    assert rebuilt.sources[2].path == "decisions/2026-06-30.md"


@pytest.mark.unit
def test_render_sources_footer_is_deterministic_and_empty_for_no_sources() -> None:
    """The footer renderer is pure: same refs in, same markdown out; empty in,
    empty string out (so the caller appends nothing).
    """
    refs = _three_refs()
    first = render_sources_footer(refs)
    second = render_sources_footer(refs)
    assert first == second
    assert first.lstrip().startswith("## Sources")
    # Each citation is numbered and carries its canonical breadcrumb.
    assert "1. " in first
    assert "sharepoint://acme-site/handbook.zip" in first
    assert render_sources_footer([]) == ""


@pytest.mark.unit
def test_footer_label_prefers_title_then_falls_back_to_path() -> None:
    """The citation label is the human title when present, else the path —
    pins ``label = ref.title or ref.path`` (a title-bearing source must NOT
    render its raw path as the label).

    Sabotage-proof (executed): mutating ``ref.title or ref.path`` to
    ``ref.title and ref.path`` makes this fail — the titled source renders its
    path instead of "Acme Handbook". Restored.
    """
    titled = render_sources_footer(
        [
            SourceRef.of(
                path="archive/handbook.zip#1536",
                source_uri="sharepoint://acme-site/handbook.zip",
                title="Acme Handbook",
            )
        ]
    )
    # The human title is the label; the synthetic chunk-key path is NOT.
    assert "Acme Handbook" in titled
    assert "1. Acme Handbook — " in titled

    # A titleless source falls back to its path as the label.
    untitled = render_sources_footer([SourceRef.of(path="notes/loose.md")])
    assert "1. notes/loose.md — " in untitled
