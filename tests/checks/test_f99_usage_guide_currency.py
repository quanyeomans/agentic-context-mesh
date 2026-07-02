"""F99 detector tests — usage-guide ↔ tool-registry currency (PLA-299).

The F99 detector
(``scripts/checks/check_f99_usage_guide_currency.py``) reads the tool
registry (``tool_capabilities()`` in the MCP server) and asserts every
registered capability is discoverable in the bundled agent usage guide via
one of its invocation tokens (``kairix <cli>`` / the bare ``<mcp_tool>`` wire
name / the bare ``<escalate_via>`` name). This is the currency lock that
closes the drift class that let ``expand`` fall out of the guide after it
shipped. Post-PLA-321 the guide is generated with the bare MCP names, so the
token scanned for is the bare ``<mcp_tool>``, not ``tool_<mcp_tool>``.

Sabotage proof (unit-level): ``test_missing_capability_is_a_violation``
seeds a synthetic registry whose ``contradict`` capability is NOT mentioned
in the synthetic guide and asserts the detector reports it. Dropping the
``not any(tok in guide_text ...)`` guard in ``collect_violations`` makes that
assertion go empty (red). The complementary
``test_present_capability_is_not_a_violation`` proves a mentioned capability
is clean, and ``test_excluded_recommender_is_not_a_violation`` proves the
flag-gated exclusion holds. ``test_real_repo_is_currently_clean`` binds the
detector to the live guide + registry so the rule stays honest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from check_f99_usage_guide_currency import (  # noqa: E402
    REPO_ROOT,
    _invocation_tokens,
    collect_violations,
)

pytestmark = pytest.mark.unit

_GUIDE_REL = "kairix/agents/usage_guide/data/agent-usage-guide.md"


def _seed_repo(tmp_path: Path, *, server_body: str, guide_text: str) -> Path:
    """Write a synthetic mini-repo (server registry + bundled guide)."""
    server = tmp_path / "kairix" / "agents" / "mcp" / "server.py"
    server.parent.mkdir(parents=True, exist_ok=True)
    server.write_text(server_body, encoding="utf-8")

    guide = tmp_path / "kairix" / "agents" / "usage_guide" / "data" / "agent-usage-guide.md"
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text(guide_text, encoding="utf-8")
    return tmp_path


def _server_with(caps: str) -> str:
    """Return a minimal ``server.py`` body wrapping the given _cap rows.

    Includes a module-level string constant so the constant-resolution path
    (``_cap(name=CONTRADICT_TOOL_NAME, ...)``) is exercised.
    """
    return (
        'CONTRADICT_TOOL_NAME = "contradict"\n\n'
        "def _cap(**kw):\n    return kw\n\n"
        "def tool_capabilities():\n"
        '    return {"capabilities": [\n'
        f"{caps}"
        "    ]}\n"
    )


def test_present_capability_is_not_a_violation(tmp_path: Path) -> None:
    """A capability whose CLI or tool token appears in the guide is clean."""
    root = _seed_repo(
        tmp_path,
        server_body=_server_with('        _cap(name="search", mcp_tool="search", cli="kairix search"),\n'),
        guide_text="Run `kairix search` to retrieve content.\n",
    )
    assert collect_violations(root) == set()


def test_missing_capability_is_a_violation(tmp_path: Path) -> None:
    """A registered capability the guide never mentions is flagged."""
    root = _seed_repo(
        tmp_path,
        server_body=_server_with(
            '        _cap(name=CONTRADICT_TOOL_NAME, mcp_tool=CONTRADICT_TOOL_NAME, cli="kairix contradict"),\n'
        ),
        guide_text="This guide mentions nothing about that capability.\n",
    )
    assert collect_violations(root) == {Path(f"{_GUIDE_REL}::contradict")}


def test_constant_resolved_capability_present_is_clean(tmp_path: Path) -> None:
    """`_cap(name=CONTRADICT_TOOL_NAME, ...)` resolves the constant and passes
    when the guide names the bare `contradict` wire name."""
    root = _seed_repo(
        tmp_path,
        server_body=_server_with(
            '        _cap(name=CONTRADICT_TOOL_NAME, mcp_tool=CONTRADICT_TOOL_NAME, cli="kairix contradict"),\n'
        ),
        guide_text="The `contradict` MCP tool checks new content.\n",
    )
    assert collect_violations(root) == set()


def test_excluded_recommender_is_not_a_violation(tmp_path: Path) -> None:
    """The flag-gated recommender is on the exclusion allowlist, so its
    absence from the guide is NOT a violation (PLA-299)."""
    root = _seed_repo(
        tmp_path,
        server_body=_server_with(
            '        _cap(name="recommend", mcp_tool="recommend_capabilities", cli="kairix recommend"),\n'
        ),
        guide_text="This guide deliberately does not advertise the recommender.\n",
    )
    assert collect_violations(root) == set()


def test_operator_only_matched_on_escalate_via_token(tmp_path: Path) -> None:
    """An operator-only capability (mcp_tool=None) is discoverable via its
    bare `<escalate_via>` token even when its CLI is a python snippet."""
    root = _seed_repo(
        tmp_path,
        server_body=_server_with(
            '        _cap(name="soak_run", mcp_tool=None, cli="python -c \'...\'", escalate_via="soak_run"),\n'
        ),
        guide_text="Operators run the `soak_run` escalation.\n",
    )
    assert collect_violations(root) == set()


def test_python_snippet_cli_is_not_a_token() -> None:
    """A bare ``python -c`` CLI is not a distinctive guide token; only the
    bare <mcp_tool> / <escalate_via> wire names are."""
    tokens = _invocation_tokens({"name": "probe_search", "mcp_tool": "probe_search", "cli": "python -c '...'"})
    assert tokens == ["probe_search"]


def test_real_repo_is_currently_clean() -> None:
    """The live bundled guide covers every registered capability (currency
    holds at HEAD). If this fails, a capability shipped without a guide row —
    add it to the guide rather than baselining the rule."""
    assert collect_violations(REPO_ROOT) == set()
