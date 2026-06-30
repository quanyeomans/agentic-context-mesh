"""Contract: CLI ↔ MCP parity for the ``prep`` operation (Phase 3c of #168).

Structural parity (lines 1-60) pins that both surfaces wire ``run_prep``
and surface the same envelope fields. Behavioural parity (lines 61+)
pins #404 — both surfaces must render the **same** ``PrepOutput`` summary
identically. The bug pattern that motivated #404 is a CLI rendering
short-circuit that suppresses the summary while MCP serialises it
truthfully. The behavioural test exercises both surfaces against the
same injected ``PrepDeps`` and asserts the LLM-emitted summary text
appears in both renderings.
"""

from __future__ import annotations

import inspect
import io
import json
import typing
from contextlib import redirect_stdout
from typing import Any

import pytest


@pytest.mark.contract
def test_cli_main_calls_run_prep() -> None:
    from kairix.agents.prep import cli

    src = inspect.getsource(cli.main)
    assert "run_prep(" in src


@pytest.mark.contract
def test_mcp_tool_prep_calls_run_prep() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_prep)
    assert "run_prep(" in src
    assert "from kairix.use_cases.prep import" in src


@pytest.mark.contract
def test_mcp_tool_prep_does_not_drive_pipeline_directly() -> None:
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_prep)
    assert "build_search_pipeline" not in src
    assert "chat_completion" not in src


@pytest.mark.contract
def test_use_case_returns_prep_output() -> None:
    from kairix.use_cases.prep import PrepOutput, run_prep

    hints = typing.get_type_hints(run_prep)
    assert hints.get("return") is PrepOutput


@pytest.mark.contract
def test_envelope_keys_match_prep_output() -> None:
    from kairix.use_cases import prep as uc

    src = inspect.getsource(uc.prep_output_to_envelope)
    for key in ("query", "tier", "summary", "tokens", "sources", "error"):
        assert f'"{key}"' in src


@pytest.mark.contract
def test_kairix_prep_command_is_registered() -> None:
    """The dispatch table in kairix/cli.py exposes the prep command."""
    from kairix.cli import COMMANDS

    assert "prep" in COMMANDS
    assert COMMANDS["prep"][0] == "kairix.agents.prep.cli"


# ---------------------------------------------------------------------------
# Behavioural parity (#404) — same PrepOutput, same rendering on both surfaces
# ---------------------------------------------------------------------------


class _StubHit:
    """Duck-typed FusedResult — ``_format_context`` reads ``title``/``path``."""

    def __init__(self, title: str, path: str) -> None:
        self.title = title
        self.path = path


class _StubBudgeted:
    """Duck-typed BudgetedResult — ``_format_context`` reads ``result``/``content``."""

    def __init__(self, title: str, path: str, content: str) -> None:
        self.result = _StubHit(title, path)
        self.content = content


class _StubSearchResult:
    """Duck-typed SearchResult — ``_format_context`` reads ``results``."""

    def __init__(self, hits: list[_StubBudgeted]) -> None:
        self.results = hits


def _build_prep_deps(summary_text: str, sources: list[tuple[str, str, str]]) -> Any:
    """Build a ``PrepDeps`` whose search returns ``sources`` and chat returns ``summary_text``.

    ``sources`` is a list of ``(title, path, content)`` tuples. Content
    strings must be ≥40 chars to clear ``_MIN_USEFUL_SNIPPET_CHARS`` on
    non-fact rows — otherwise ``_format_context`` drops them and the
    early-return path fires.
    """
    from kairix.use_cases.prep import PrepDeps

    hits = [_StubBudgeted(t, p, c) for (t, p, c) in sources]
    sr = _StubSearchResult(hits)

    def fake_search(**_kwargs: Any) -> Any:
        return sr

    def fake_chat(**_kwargs: Any) -> str:
        return summary_text

    return PrepDeps(search_fn=fake_search, chat_fn=fake_chat)


@pytest.mark.contract
def test_cli_and_mcp_render_same_summary_text() -> None:
    """Same ``PrepOutput`` → CLI stdout + MCP envelope both surface the summary.

    The bug pattern #404 was a CLI rendering short-circuit that emitted
    "No relevant content found" while MCP returned a useful summary on
    the same inputs. This test pins parity: with identical injected
    ``PrepDeps`` (same fake search + same fake chat returning the same
    summary), CLI's stdout MUST contain the summary text and MCP's
    envelope MUST contain the same text under ``summary``.

    Sabotage proof (run locally 2026-06-02): replace
    ``kairix/agents/prep/cli.py::format_text``'s ``out.summary,`` line
    with ``"No relevant content found",`` — the test fails on
    ``"agent-alpha is the VP" in cli_stdout``. Restored after proof.
    """
    from kairix.agents.mcp.server import tool_prep
    from kairix.agents.prep.cli import main as cli_main
    from kairix.use_cases.prep import reset_prep_summary_cache

    summary = "agent-alpha is the VP of People at Acme. She owns the onboarding workflow."
    sources = [
        (
            "onboarding-notes",
            "notes/onboarding.md",
            "Onboarding handoff: agent-alpha owns the new-hire intake from week one.",
        ),
        (
            "role-card",
            "people/agent-alpha.md",
            "agent-alpha is the VP of People at Acme. Reports to the CEO.",
        ),
    ]

    # Reset the process-shared prep summary cache so a stale entry from a
    # prior test can't shadow the chat_fn we just wired.
    reset_prep_summary_cache()
    deps_cli = _build_prep_deps(summary, sources)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["what is agent-alpha's role?"], deps=deps_cli)
    cli_stdout = buf.getvalue()
    assert rc == 0, f"CLI exited {rc}; stdout was:\n{cli_stdout}"
    assert summary in cli_stdout, f"CLI stdout missing summary text; got:\n{cli_stdout}"
    # Sources must surface too — both surfaces.
    assert "onboarding-notes" in cli_stdout
    assert "role-card" in cli_stdout

    # Reset again so the MCP call hits a fresh cache.
    reset_prep_summary_cache()
    deps_mcp = _build_prep_deps(summary, sources)
    envelope = tool_prep(query="what is agent-alpha's role?", deps=deps_mcp)

    assert envelope["summary"] == summary, f"MCP envelope summary mismatch: {envelope!r}"
    # PLA-274 / #437 — sources are now resolvable SourceRef breadcrumb dicts,
    # not bare title strings. The human title rides on ``title``; the
    # resolvable pointer is ``path`` / ``source_uri`` (source_uri falls back
    # to path when the stub carries no connector URI).
    src_titles = [s["title"] for s in envelope["sources"]]
    src_uris = [s["source_uri"] for s in envelope["sources"]]
    assert src_titles == ["onboarding-notes", "role-card"], f"MCP envelope source titles mismatch: {envelope!r}"
    assert src_uris == ["notes/onboarding.md", "people/agent-alpha.md"], (
        f"MCP envelope source uris mismatch: {envelope!r}"
    )
    assert envelope["error"] == "", f"MCP envelope unexpectedly carries error: {envelope!r}"


@pytest.mark.contract
def test_cli_json_envelope_equals_mcp_envelope() -> None:
    """CLI ``--json`` and MCP ``tool_prep`` must emit byte-identical envelopes.

    Pins the second half of #404 parity: the JSON envelope path. If
    either surface adds/removes a field or applies post-processing, the
    dict comparison fails. ``prep_output_to_envelope`` is the only
    serialiser both surfaces are allowed to use.
    """
    from kairix.agents.mcp.server import tool_prep
    from kairix.agents.prep.cli import main as cli_main
    from kairix.use_cases.prep import reset_prep_summary_cache

    summary = "concise grounded summary"
    sources = [
        (
            "doc-one",
            "notes/doc-one.md",
            "This is a non-fact chunk snippet long enough to clear the 40-char floor.",
        ),
    ]

    reset_prep_summary_cache()
    deps_cli = _build_prep_deps(summary, sources)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(["topic?", "--json"], deps=deps_cli)
    assert rc == 0, f"CLI exited {rc}; stdout was:\n{buf.getvalue()}"
    cli_envelope = json.loads(buf.getvalue())

    reset_prep_summary_cache()
    deps_mcp = _build_prep_deps(summary, sources)
    mcp_envelope = tool_prep(query="topic?", deps=deps_mcp)

    assert cli_envelope == mcp_envelope, (
        f"CLI ↔ MCP envelope divergence (#404):\nCLI: {cli_envelope!r}\nMCP: {mcp_envelope!r}"
    )


@pytest.mark.contract
def test_cli_format_text_renders_summary_field_verbatim() -> None:
    """``format_text`` must surface ``out.summary`` exactly — no content-based filter.

    The structural pin for #404: ``format_text`` may not branch on
    ``out.summary``'s content (e.g. "if 'no relevant' in summary:
    print(...)"). The only conditionals allowed are on ``out.error`` and
    ``out.sources`` (both already in place). Anything else is a CLI-only
    short-circuit that diverges from MCP's envelope surfaceing.

    Sabotage proof: add ``if "No relevant" in out.summary: return "no
    content"`` to ``format_text`` — the assertion below fails because
    the source no longer contains a bare ``out.summary,`` reference in
    the lines list. Restored.
    """
    from kairix.agents.prep import cli

    src = inspect.getsource(cli.format_text)
    # Allowed conditionals: error (line 90) + sources (line 98).
    # Forbidden: any ``if`` that inspects ``out.summary``.
    forbidden_patterns = [
        "if not out.summary",
        "if out.summary ==",
        "out.summary in",
        "in out.summary",
        '"No relevant content found"',
        '"no relevant content"',
    ]
    for pattern in forbidden_patterns:
        assert pattern not in src, (
            f"format_text contains forbidden CLI-side short-circuit on summary content: "
            f"{pattern!r}. The MCP envelope renders ``out.summary`` verbatim — the CLI must too. "
            f"#404: align rendering, do not add new conditions."
        )
    # Positive pin: ``out.summary,`` appears as a bare list element (no
    # content-based gate around it).
    assert "out.summary," in src or "out.summary\n" in src, (
        "format_text must surface out.summary as a bare list element — "
        "see kairix/agents/prep/cli.py::format_text for the canonical shape."
    )
