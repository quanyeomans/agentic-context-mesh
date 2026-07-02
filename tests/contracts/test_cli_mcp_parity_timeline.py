"""Contract: CLI ↔ MCP parity for the ``timeline`` operation.

Phase 1 of #168 (CLI/MCP feature parity) extracted timeline business
logic into ``kairix.use_cases.timeline.run_timeline``. Both surfaces
are now thin adapters around it. This contract pins the parity:

  - Same use case is wired into the CLI's ``main()`` and the MCP's
    ``tool_timeline``.
  - Both adapters call ``run_timeline`` with parameter pass-through —
    no surface-specific business logic. So when the use case changes,
    both surfaces update together.

If you find yourself re-implementing date extraction, query rewriting,
or backend dispatch outside ``run_timeline``, this contract should
fail — that's the smell #163 surfaced.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest


@pytest.mark.contract
def test_cli_main_calls_run_timeline_use_case() -> None:
    """The CLI's ``main()`` is a thin adapter that defers to the use case.

    Post-F1: ``main()`` defers to a configurable ``timeline_runner``
    whose production default wires ``run_timeline``. The contract pins
    all three halves through public source: the module imports
    ``run_timeline``, ``main()`` defers to ``timeline_runner(...)``, and
    the production default factory used by ``main()`` actually invokes
    ``run_timeline``.

    Behavioural sabotage: replace ``main()``'s body with a direct call
    to ``run_timeline(...)`` (skipping the ``timeline_runner`` seam) →
    behavioural test below (``test_cli_main_uses_injected_timeline_runner``)
    fires; the source assertion ``"timeline_runner(" in main_src`` here
    fires too.
    """
    from kairix.core.temporal import cli

    module_src = inspect.getsource(cli)
    assert "from kairix.use_cases.timeline import run_timeline" in module_src
    assert "run_timeline(" in module_src

    main_src = inspect.getsource(cli.main)
    assert "timeline_runner(" in main_src, (
        "main() must defer to the configured timeline_runner — not call run_timeline directly"
    )


@pytest.mark.contract
def test_cli_main_uses_injected_timeline_runner() -> None:
    """Behavioural contract: ``main()`` must invoke the ``timeline_runner``
    DI seam (not bypass it with a direct ``run_timeline`` call).

    Drives ``main()`` with a counting runner via the public
    ``timeline_runner`` kwarg seam (F5 + F1 clean). If ``main()``
    ever stops routing through ``timeline_runner``, the counter stays
    at 0 and this test fails.

    Sabotage: change ``main()`` to call ``run_timeline(...)`` directly →
    counter stays at 0 → this test fires.
    """
    from kairix.core.temporal import cli
    from kairix.use_cases.timeline import TimelineResult

    calls: list[dict[str, Any]] = []

    def _counting_runner(*args: Any, **kwargs: Any) -> TimelineResult:
        calls.append({"args": args, "kwargs": kwargs})
        return TimelineResult(
            original_query=args[0] if args else kwargs.get("query", ""),
            rewritten_query="",
            is_temporal=False,
            fell_back=False,
            time_window={},
        )

    cli.main(
        ["test query", "--json", "--limit", "5"],
        timeline_runner=_counting_runner,
    )
    assert len(calls) == 1


@pytest.mark.contract
def test_default_timeline_runner_factory_wires_run_timeline() -> None:
    """The production default for the ``timeline_runner`` seam invokes
    ``run_timeline`` — pinned at module source level so we don't reach
    into the private factory's attribute (F5).

    Sabotage: change the default factory body to drop the
    ``run_timeline(...)`` call → ``"return run_timeline(" in module_src``
    no longer holds → this fires.
    """
    from kairix.core.temporal import cli

    module_src = inspect.getsource(cli)
    # The default factory must contain an actual call to ``run_timeline(``
    # in a ``return`` position — that's the production wiring contract.
    assert "return run_timeline(" in module_src, (
        "CLI module must contain ``return run_timeline(...)`` in its production timeline-runner default factory."
    )


@pytest.mark.contract
def test_mcp_tool_timeline_calls_run_timeline_use_case() -> None:
    """The MCP's ``tool_timeline`` is a thin adapter that defers to the use case."""
    from kairix.agents.mcp import server

    src = inspect.getsource(server.tool_timeline)
    assert "from kairix.use_cases.timeline import run_timeline" in src
    assert "run_timeline(" in src


@pytest.mark.contract
def test_cli_does_not_call_query_temporal_chunks_directly() -> None:
    """CLI must NOT bypass the use case to hit the temporal index directly.

    Pre-Phase 1, the CLI imported ``query_temporal_chunks`` and used a
    different code path than the MCP. The use case now owns that
    dispatch, so neither adapter should reach past it.
    """
    from kairix.core.temporal import cli

    src = inspect.getsource(cli)
    assert "query_temporal_chunks" not in src, (
        "CLI bypasses run_timeline — see #163. All temporal-chunks access must go via the use case."
    )


@pytest.mark.contract
def test_mcp_tool_timeline_signature_matches_use_case_passthrough() -> None:
    """The MCP adapter exposes the same uniform parameters as the use case.

    The MCP signature uses string ``anchor_date`` (JSON wire format) which
    the adapter parses to ``date`` before delegating. All other parameters
    pass through unchanged.

    Exclusion ``deps`` is the test-DI seam (a ``TimelineDeps`` injection
    point), not an operator-facing arg — mirrors the search / entity / expand
    parity tests, which all subtract ``deps`` from the surface set (PLA-322
    brought the timeline adapter to the same ``deps``-seam parity).
    """
    from kairix.agents.mcp.server import tool_timeline
    from kairix.use_cases.timeline import run_timeline

    mcp_params = set(inspect.signature(tool_timeline).parameters) - {"deps"}

    # MCP exposes the JSON-friendly wire surface.
    expected_mcp = {"query", "anchor_date", "agent", "scope"}
    assert mcp_params == expected_mcp

    # And the use case must accept the same kwargs as a typed superset.
    use_case_params_check = set(inspect.signature(run_timeline).parameters)
    assert expected_mcp.issubset(use_case_params_check)


@pytest.mark.contract
def test_cli_argparse_exposes_every_mcp_user_facing_arg() -> None:
    """CLI argparse must expose every kwarg that MCP tool_timeline exposes.

    Production sweep 2026-06-03 surfaced the divergence: MCP exposed
    ``agent`` + ``anchor_date`` but the CLI's argparse silently rejected
    them. This contract pins the parity at the argument-shape level —
    not just "both call run_timeline" but "both surface the same args".

    Sabotage proof: delete the ``--agent`` argparse line from
    ``kairix.core.temporal.cli.build_parser`` — this test fails with
    ``agent`` reported as missing from CLI surface. Restored.

    Exclusion ``scope`` is the MCP-only Scope enum; CLI ergonomics use
    --since/--until/--type to bound the same query window so a verbatim
    ``--scope`` flag would be confusing. Exclude from parity expectation
    here and document the lift on the use-case side. ``deps`` is likewise
    excluded — it's the ``TimelineDeps`` test-DI seam (PLA-322), not an
    operator-facing arg, matching the search / entity / expand parity tests.
    """
    from kairix.agents.mcp.server import tool_timeline
    from kairix.core.temporal import cli

    mcp_params = set(inspect.signature(tool_timeline).parameters) - {"scope", "deps"}
    parser = cli.build_parser()
    cli_dests = {action.dest for action in parser._actions if action.dest not in {"help"}}

    # MCP param names like "anchor_date" should map to CLI dest "anchor_date"
    # (argparse converts ``--anchor-date`` to ``anchor_date`` via the dest).
    missing = mcp_params - cli_dests
    assert not missing, (
        f"CLI argparse missing MCP-equivalent args: {sorted(missing)}. "
        f"CLI dests: {sorted(cli_dests)}. MCP params (minus scope): {sorted(mcp_params)}."
    )

    # Use case exposes the typed superset.
    from kairix.use_cases.timeline import run_timeline

    use_case_params = set(inspect.signature(run_timeline).parameters)
    assert {"query", "anchor_date", "agent", "scope"}.issubset(use_case_params)


@pytest.mark.contract
def test_use_case_returns_documented_result_dataclass() -> None:
    """``run_timeline`` returns ``TimelineResult`` — both adapters serialise from this."""
    # PEP 563 (``from __future__ import annotations``) keeps annotations as
    # strings; resolve via ``typing.get_type_hints`` so the assertion sees
    # the real class object.
    import typing

    from kairix.use_cases.timeline import TimelineResult, run_timeline

    hints = typing.get_type_hints(run_timeline)
    assert hints.get("return") is TimelineResult


@pytest.mark.contract
def test_mcp_envelope_keys_match_run_timeline_result_fields() -> None:
    """The MCP JSON envelope keys are exactly the use case's TimelineResult fields,
    keyed `path/title/snippet/score` for each hit. If either surface drifts, this fails.

    Post-#412: both CLI ``--json`` and MCP delegate to ``timeline_output_to_envelope``
    in ``kairix.use_cases.timeline`` — the single source of truth for the envelope
    shape. Check the canonical helper, not the per-adapter inlined dicts.
    """
    from kairix.use_cases import timeline as timeline_uc

    src = inspect.getsource(timeline_uc.timeline_output_to_envelope)
    for key in (
        "original_query",
        "rewritten_query",
        "is_temporal",
        "fell_back",
        "time_window",
        "results",
        "error",
    ):
        assert f'"{key}"' in src, f"timeline_output_to_envelope missing key {key!r}"
    for hit_key in ("path", "title", "snippet", "score"):
        assert f'"{hit_key}"' in src, f"timeline_output_to_envelope hit envelope missing key {hit_key!r}"

    # Sanity: both surfaces delegate to the canonical helper (#412 SoT).
    # Module-source pin (no private-attribute access — F5).
    from kairix.agents.mcp import server as mcp_server
    from kairix.core.temporal import cli as cli_mod

    assert "timeline_output_to_envelope" in inspect.getsource(mcp_server.tool_timeline), (
        "MCP tool_timeline must delegate envelope construction to timeline_output_to_envelope (#412)"
    )
    cli_module_src = inspect.getsource(cli_mod)
    assert "timeline_output_to_envelope" in cli_module_src, "CLI must delegate to timeline_output_to_envelope (#412)"


@pytest.mark.contract
def test_cli_json_envelope_matches_canonical_use_case_shape() -> None:
    """Behavioural pin: CLI ``--as-json`` envelope ≡ canonical envelope + ``limit``.

    Drives ``main()`` with a runner returning a known ``TimelineResult``,
    captures stdout, and asserts the JSON envelope shape matches
    ``timeline_output_to_envelope`` output (the canonical SoT) with the
    CLI-only ``limit`` overlay. F5 + F1 clean — public seam only.

    Sabotage: have the CLI inline its own envelope dict (skipping
    ``timeline_output_to_envelope``) and drop a required key like
    ``results`` → this assertion fires.
    """
    import io
    import json
    from contextlib import redirect_stdout

    from kairix.core.temporal import cli
    from kairix.use_cases.timeline import TimelineResult, timeline_output_to_envelope

    fake_result = TimelineResult(
        original_query="meeting last week",
        rewritten_query="meeting in April 2026",
        is_temporal=True,
        fell_back=False,
        time_window={"start": "2026-04-01", "end": "2026-04-30"},
    )

    def _runner(*_a: Any, **_k: Any) -> TimelineResult:
        return fake_result

    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(
            ["topic", "--json", "--limit", "7"],
            timeline_runner=_runner,
        )
    payload = json.loads(out.getvalue())
    canonical = timeline_output_to_envelope(fake_result)
    canonical["limit"] = 7
    assert payload == canonical
