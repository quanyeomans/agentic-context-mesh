"""Unit coverage for the operator-only escalation command / runtime builders.

The eight escalation stubs shape their OperatorOnlyCapability envelope through
one shared code path (``_escalation_envelope``), reading per-capability data
from ``_ESCALATION_SPECS``. The command + runtime resolvers carry the only
branch/arithmetic logic in the module — the ``limit == 0`` embed flag, the
``verb == "list"`` cc-pair suffix, and the soak/probe-burst runtime scaling.

These tests pin those resolvers at the operator-visible envelope boundary with
EXACT-equality assertions (not substring), so a flipped comparison or altered
arithmetic is caught. They import from ``kairix.agents.mcp.tools.operator``
(the public adapter module — F5-clean: public module, public ``tool_*`` names)
so the mutation-parity impacted-test selection anchors this file to the
operator module and runs it against the mutated conditionals.
"""

from __future__ import annotations

import pytest

from kairix.agents.mcp.tools.operator import (
    tool_benchmark_run,
    tool_cc_pair,
    tool_embed,
    tool_probe_burst,
    tool_soak_run,
)

pytestmark = pytest.mark.unit


def test_embed_default_command_has_no_limit_flag() -> None:
    """limit=0 → flag-free command.

    Sabotage: flip ``limit == 0`` to ``!=`` in ``_embed_command`` → the default
    renders ``kairix embed --limit 0`` and this exact-equality assertion fires.
    """
    assert tool_embed()["operator_command"] == "kairix embed"


def test_embed_command_threads_limit_flag() -> None:
    """limit=5 → ``--limit 5`` suffix.

    Sabotage: flip ``limit == 0`` to ``!=`` → limit=5 renders flag-free
    ``kairix embed`` and this exact-equality assertion fires.
    """
    assert tool_embed(limit=5)["operator_command"] == "kairix embed --limit 5"


def test_cc_pair_list_command_has_no_id_placeholder() -> None:
    """verb=list → no ``--id`` placeholder.

    Sabotage: flip ``verb == "list"`` to ``!=`` in ``_cc_pair_command`` → the
    read-only ``list`` verb renders ``kairix cc-pair list --id <id>`` and this
    exact-equality assertion fires (a substring ``in`` check would NOT).
    """
    assert tool_cc_pair()["operator_command"] == "kairix cc-pair list"


def test_cc_pair_mutating_verb_carries_id_placeholder() -> None:
    """A mutating verb → ``--id <id>`` placeholder.

    Sabotage: flip ``verb == "list"`` to ``!=`` → ``pause`` renders the
    placeholder-free ``kairix cc-pair pause`` and this assertion fires.
    """
    assert tool_cc_pair(verb="pause")["operator_command"] == "kairix cc-pair pause --id <id>"


def test_soak_runtime_scales_with_repeat() -> None:
    """soak runtime = 60 * repeat.

    Sabotage: change ``60 * int(params["repeat"])`` to ``60 + …`` → repeat=3
    yields 63 not 180 and this assertion fires.
    """
    assert tool_soak_run(repeat=3)["expected_runtime_seconds"] == 180
    assert tool_soak_run(repeat=5)["expected_runtime_seconds"] == 300


def test_probe_burst_runtime_scales_with_total_queries() -> None:
    """probe-burst runtime = max(30, total_queries // 5).

    Sabotage: change ``// 5`` to ``// 50`` → total_queries=1000 yields the
    floor 30 not 200 and this assertion fires.
    """
    assert tool_probe_burst(total_queries=200)["expected_runtime_seconds"] == 40
    assert tool_probe_burst(total_queries=1000)["expected_runtime_seconds"] == 200
    # Below the floor, the 30s minimum holds.
    assert tool_probe_burst(total_queries=50)["expected_runtime_seconds"] == 30


def test_benchmark_command_names_the_suite() -> None:
    """benchmark command carries the exact ``--suite`` flag."""
    assert tool_benchmark_run(suite="reflib")["operator_command"] == "kairix benchmark run --suite reflib"


def test_soak_command_forwards_suite_and_repeat() -> None:
    """soak command forwards both args into the Python-API one-liner."""
    cmd = tool_soak_run(suite="reflib", repeat=3)["operator_command"]
    assert cmd == "python -c 'from kairix.quality.soak import run_soak; print(run_soak(suite=\"reflib\", repeat=3))'"
