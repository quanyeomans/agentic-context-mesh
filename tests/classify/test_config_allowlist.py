"""Config-driven agent allowlist for classify (#472).

The classify surface (router / rules / judge / CLI) historically
validated agents against a hardcoded legacy frozenset, so any agent
declared in the operator's ``agents:`` config block — the same block
``kairix onboard scan`` emits and ``kairix doctor agent`` validates —
was rejected. These tests pin the fix:

  - configured agent names are accepted (union with the legacy set);
  - unconfigured agents are still rejected, and the rejection message
    carries the F21 ``fix:`` / ``next:`` affordance;
  - no ``agents:`` block → exactly the legacy behaviour (default-safe).

F1-clean: the parsed config is injected through the ``config=`` seam
that already exists on ``resolve_target_path`` and is now threaded
through ``classify_content`` / ``classify_with_llm`` / the CLI ``main``.
No monkeypatching, no env vars.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from kairix.core.classify.judge import classify_with_llm
from kairix.core.classify.router import (
    VALID_AGENTS,
    resolve_target_path,
    valid_agents,
)
from kairix.core.classify.rules import classify_content
from tests.fakes import FakeLLMBackend

pytestmark = pytest.mark.unit


def _scope_config(*names: str) -> dict[str, object]:
    """Build an ``agents:`` mapping block (the shape ``kairix onboard
    scan --yaml`` emits and ``load_agent_scopes`` parses)."""
    return {
        "agents": {
            name: {
                "harness": "claude-code",
                "surfaces": [
                    {"path": f"04-Agent-Knowledge/{name}", "label": "memory"},
                ],
            }
            for name in names
        }
    }


# ---------------------------------------------------------------------------
# valid_agents — the allowlist function itself
# ---------------------------------------------------------------------------


def test_valid_agents_unions_configured_names_with_legacy_set() -> None:
    """A configured agent joins the allowlist; the legacy names survive.

    Sabotage: drop the ``| VALID_AGENTS`` union (or the configured-name
    read) in ``valid_agents`` → one of the two membership assertions fails.
    """
    allowed = valid_agents(config=_scope_config("agent-alpha"))
    assert "agent-alpha" in allowed
    assert VALID_AGENTS <= allowed, "legacy agents must remain valid (default-safe)"


def test_valid_agents_with_empty_config_is_exactly_the_legacy_set() -> None:
    """No ``agents:`` block → the legacy frozenset, nothing more.

    Sabotage: make ``valid_agents`` inject any extra default name →
    the equality assertion fails.
    """
    assert valid_agents(config={}) == VALID_AGENTS


def test_valid_agents_supports_legacy_list_schema() -> None:
    """The registry-style ``agents:`` LIST schema also contributes names.

    Older deployments declare ``agents:`` as a list of ``{name, write_path}``
    mappings (the shape ``kairix config validate`` checks). Both schema
    generations must feed the allowlist.

    Sabotage: drop the list-shape branch from the configured-name reader →
    the membership assertion fails.
    """
    config = {"agents": [{"name": "agent-beta", "write_path": "04-Agent-Knowledge/agent-beta"}]}
    allowed = valid_agents(config=config)
    assert "agent-beta" in allowed


def test_valid_agents_tolerates_malformed_agents_block() -> None:
    """A scalar ``agents:`` value must not crash validation — fall back
    to the legacy set so a config typo degrades, not detonates.

    Sabotage: let the configured-name reader raise on a non-dict/non-list
    ``agents:`` value → this call raises instead of returning the legacy set.
    """
    assert valid_agents(config={"agents": "oops"}) == VALID_AGENTS


# ---------------------------------------------------------------------------
# classify_content (rules) — configured accept / unconfigured reject
# ---------------------------------------------------------------------------


def test_classify_content_accepts_configured_agent() -> None:
    """An agent declared in the config ``agents:`` block classifies fine.

    Sabotage: revert ``classify_content`` to the hardcoded
    ``VALID_AGENTS`` check → this raises ValueError and the test fails.
    """
    result = classify_content(
        "rule: never store credentials in plaintext",
        agent="agent-alpha",
        config=_scope_config("agent-alpha"),
    )
    assert result.type == "procedural-rule"


def test_classify_content_rejects_unconfigured_agent_with_f21_affordance() -> None:
    """An unknown agent is rejected and the message tells the operator
    exactly how to fix it (F21 ``fix:`` / ``next:`` markers) and lists
    the actually-valid names.

    Sabotage: drop the fix:/next: suffix from the rejection message →
    the marker assertions fail.
    """
    with pytest.raises(ValueError, match="Invalid agent") as exc_info:
        classify_content(
            "rule: never store credentials in plaintext",
            agent="agent-omega",
            config=_scope_config("agent-alpha"),
        )
    message = str(exc_info.value)
    assert "agent-alpha" in message, "must list the actually-valid names"
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in message
    assert "next: re-run kairix doctor agent --all" in message


def test_classify_content_with_no_config_keeps_legacy_behaviour() -> None:
    """Default-safe: with an empty config the legacy set still works and
    unknown names are still rejected.

    Sabotage: make the empty-config branch return an empty allowlist →
    the ``builder`` call raises and the first assertion fails.
    """
    result = classify_content("rule: never push to main directly", agent="builder", config={})
    assert result.type == "procedural-rule"
    with pytest.raises(ValueError, match="Invalid agent"):
        classify_content("rule: anything", agent="agent-alpha", config={})


# ---------------------------------------------------------------------------
# resolve_target_path (router) — config-driven validation
# ---------------------------------------------------------------------------


def test_resolve_target_path_accepts_configured_agent() -> None:
    """The router resolves a path for a configured agent.

    Sabotage: revert the router's validation to the hardcoded set →
    this raises ValueError and the test fails.
    """
    path = resolve_target_path(
        "agent-alpha",
        "procedural-rule",
        config=_scope_config("agent-alpha"),
    )
    assert "agent-alpha" in path


def test_resolve_target_path_rejects_unconfigured_agent_with_f21_affordance() -> None:
    """Router rejection carries the same F21 affordance as the rules layer.

    Sabotage: leave the router's old message format in place → the
    fix:-marker assertion fails.
    """
    with pytest.raises(ValueError, match="Invalid agent") as exc_info:
        resolve_target_path("agent-omega", "procedural-rule", config=_scope_config("agent-alpha"))
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in str(exc_info.value)


# ---------------------------------------------------------------------------
# classify_with_llm (judge) — config threaded through
# ---------------------------------------------------------------------------


def test_classify_with_llm_accepts_configured_agent() -> None:
    """The LLM judge accepts a configured agent and resolves its path.

    Sabotage: revert the judge's validation to the hardcoded set →
    this raises ValueError and the test fails.
    """
    backend = FakeLLMBackend(
        chat_response=json.dumps({"type": "procedural-rule", "confidence": 0.9, "reason": "normative"})
    )
    result = classify_with_llm(
        "never do the thing",
        agent="agent-alpha",
        llm_backend=backend,
        config=_scope_config("agent-alpha"),
    )
    assert result.type == "procedural-rule"
    assert "agent-alpha" in result.target_path


def test_classify_with_llm_rejects_unconfigured_agent_with_f21_affordance() -> None:
    """Judge rejection carries the F21 affordance and never calls the LLM.

    Sabotage: move the agent validation below the LLM call → the
    ``chat_calls == []`` assertion fails.
    """
    backend = FakeLLMBackend(chat_response="{}")
    with pytest.raises(ValueError, match="Invalid agent") as exc_info:
        classify_with_llm(
            "ambiguous content",
            agent="agent-omega",
            llm_backend=backend,
            config=_scope_config("agent-alpha"),
        )
    assert "next: re-run kairix doctor agent --all" in str(exc_info.value)
    assert backend.chat_calls == [], "judge must reject before any LLM call"


# ---------------------------------------------------------------------------
# classify CLI — config seam end-to-end through the public main()
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], config: dict[str, object]) -> tuple[str, str, int]:
    """Drive ``kairix classify`` in-process with an injected config."""
    from kairix.core.classify.cli import main

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    exit_code = 0
    try:
        with redirect_stdout(stdout_capture), redirect_stderr(stderr_capture):
            main(args, config=config)
    except SystemExit as e:
        exit_code = e.code or 0
    return stdout_capture.getvalue(), stderr_capture.getvalue(), exit_code


def test_classify_cli_accepts_configured_agent() -> None:
    """``kairix classify --agent agent-alpha`` succeeds when agent-alpha
    is declared in the config ``agents:`` block — the exact #472 symptom.

    Sabotage: revert the CLI's validation to the hardcoded set → exit
    code is 1 and the stdout JSON assertion fails.
    """
    stdout, _stderr, exit_code = _run_cli(
        ["rule: never skip the gate", "--agent", "agent-alpha", "--no-llm"],
        config=_scope_config("agent-alpha"),
    )
    assert exit_code == 0
    parsed = json.loads(stdout.strip())
    assert parsed["type"] == "procedural-rule"
    assert "agent-alpha" in parsed["target_path"]


def test_classify_cli_rejects_unconfigured_agent_with_f21_stderr() -> None:
    """The CLI rejection stderr lists valid names + the F21 affordance.

    Sabotage: drop the fix:/next: suffix from the CLI error print →
    the marker assertion fails.
    """
    _stdout, stderr, exit_code = _run_cli(
        ["anything", "--agent", "agent-omega", "--no-llm"],
        config=_scope_config("agent-alpha"),
    )
    assert exit_code == 1
    assert "agent-omega" in stderr
    assert "agent-alpha" in stderr, "stderr must list the actually-valid names"
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in stderr
    assert "next: re-run kairix doctor agent --all" in stderr
