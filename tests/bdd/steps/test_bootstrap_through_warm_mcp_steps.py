"""Step definitions for ``bootstrap_through_warm_mcp.feature``.

PR 2.3 / #421 — bootstrap envelope-to-text composer + ``--json`` flag.

Composition rule (F46): steps drive through the CLI ``main`` entry
point with ``deps=BootstrapDeps(...)`` injected; the envelope helpers
(``bootstrap_output_to_envelope`` + ``BootstrapOutput.from_envelope``)
are the public seam tested directly — no pipeline / strategy
construction.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.bootstrap_cli import main as bootstrap_main
from kairix.use_cases.bootstrap import (
    BootstrapDeps,
    BootstrapHealth,
    BootstrapOutput,
    MemoryEntry,
    bootstrap_output_to_envelope,
    bootstrap_output_to_markdown,
)

pytestmark = pytest.mark.bdd

scenarios("../features/bootstrap_through_warm_mcp.feature")


@dataclass
class _BootstrapWarmCtx:
    original: BootstrapOutput | None = None
    rebuilt: BootstrapOutput | None = None
    deps: BootstrapDeps | None = None
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    parsed_envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def bootstrap_warm_ctx() -> _BootstrapWarmCtx:
    return _BootstrapWarmCtx()


def _seed_minimal_vault(root: Path, agent: str) -> None:
    """Lay out the minimum vault structure run_bootstrap reads."""
    agent_dir = root / "04-Agent-Knowledge" / agent
    (agent_dir / "memory").mkdir(parents=True, exist_ok=True)
    (agent_dir / "Board.md").write_text("priorities: ship", encoding="utf-8")
    (agent_dir / "Goals.md").write_text("- ship the composer", encoding="utf-8")
    (agent_dir / "profile.md").write_text("# Shape\n", encoding="utf-8")


def _healthy_deps_for(root: Path) -> BootstrapDeps:
    return BootstrapDeps(
        document_root_fn=lambda: root,
        secrets_loaded_fn=lambda: True,
        embed_backend_available_fn=lambda: True,
        bm25_index_available_fn=lambda: True,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — round-trip parity
# ---------------------------------------------------------------------------


@given(parsers.parse('a bootstrap result with role "{role}" board "{board}" and one memory entry'))
def _seed_bootstrap_result(bootstrap_warm_ctx: _BootstrapWarmCtx, role: str, board: str) -> None:
    bootstrap_warm_ctx.original = BootstrapOutput(
        agent="agent-alpha",
        role=role,
        board=board,
        recent_memory=[MemoryEntry(date="2026-05-14", content="today: progress")],
        active_goals=["land PR 2.3"],
        health=BootstrapHealth(),
        next_action="Read your Board for current priorities.",
    )


@when("the bootstrap result is converted to an MCP envelope and back via from_envelope")
def _roundtrip_bootstrap_envelope(bootstrap_warm_ctx: _BootstrapWarmCtx) -> None:
    assert bootstrap_warm_ctx.original is not None
    envelope = bootstrap_output_to_envelope(bootstrap_warm_ctx.original)
    bootstrap_warm_ctx.rebuilt = BootstrapOutput.from_envelope(envelope)


@then("the round-tripped markdown is byte-identical to the original")
def _assert_markdown_byte_identical(bootstrap_warm_ctx: _BootstrapWarmCtx) -> None:
    assert bootstrap_warm_ctx.original is not None
    assert bootstrap_warm_ctx.rebuilt is not None
    original_text = bootstrap_output_to_markdown(bootstrap_warm_ctx.original)
    rebuilt_text = bootstrap_output_to_markdown(bootstrap_warm_ctx.rebuilt)
    assert original_text == rebuilt_text, (
        f"warm-MCP markdown path drifted from in-process:\n"
        f"--- in-process ---\n{original_text!r}\n--- warm-MCP ---\n{rebuilt_text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — --json flag on the CLI
# ---------------------------------------------------------------------------


@given(parsers.parse('a bootstrap use case that returns role "{role}" for "{agent}"'))
def _seed_bootstrap_deps(bootstrap_warm_ctx: _BootstrapWarmCtx, role: str, agent: str, tmp_path: Path) -> None:
    _seed_minimal_vault(tmp_path, agent)
    # Overwrite profile.md so the role line matches the table value.
    (tmp_path / "04-Agent-Knowledge" / agent / "profile.md").write_text(f"# {role}\n", encoding="utf-8")
    bootstrap_warm_ctx.deps = _healthy_deps_for(tmp_path)


@when("the operator runs the bootstrap CLI with json mode for the agent")
def _run_bootstrap_json(bootstrap_warm_ctx: _BootstrapWarmCtx) -> None:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    bootstrap_warm_ctx.exit_code = bootstrap_main(
        ["agent-alpha", "--json"],
        out=out_buf,
        err=err_buf,
        deps=bootstrap_warm_ctx.deps,
    )
    bootstrap_warm_ctx.stdout = out_buf.getvalue()
    bootstrap_warm_ctx.stderr = err_buf.getvalue()


@then(parsers.parse("bootstrap stdout is valid JSON containing keys agent, role, and health"))
def _assert_stdout_is_envelope_json(bootstrap_warm_ctx: _BootstrapWarmCtx) -> None:
    try:
        parsed = json.loads(bootstrap_warm_ctx.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"stdout was not valid JSON: {exc}\n--- stdout ---\n{bootstrap_warm_ctx.stdout!r}")
    assert isinstance(parsed, dict), f"envelope must be a dict, got {type(parsed).__name__}"
    for key in ("agent", "role", "health"):
        assert key in parsed, f"envelope missing key {key!r}: {sorted(parsed.keys())}"
    bootstrap_warm_ctx.parsed_envelope = parsed


@then(parsers.parse("the bootstrap CLI exits with status {code:d}"))
def _assert_exit(bootstrap_warm_ctx: _BootstrapWarmCtx, code: int) -> None:
    assert bootstrap_warm_ctx.exit_code == code, (
        f"expected exit {code}, got {bootstrap_warm_ctx.exit_code}; "
        f"stdout={bootstrap_warm_ctx.stdout[:200]!r} stderr={bootstrap_warm_ctx.stderr[:200]!r}"
    )
