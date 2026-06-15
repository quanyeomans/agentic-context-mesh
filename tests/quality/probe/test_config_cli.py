"""Unit tests for :func:`kairix.quality.probe.config_cli.main`.

Drives the argparse + JSON-emit surface of ``kairix probe-config`` —
the operator-facing CLI introduced by #provider-plugin-arch IM-9.
The CLI:

- resolves a provider name via ``--provider`` or the injected
  ``env_provider_lookup`` callable (defaults to
  :func:`kairix.paths.provider_name`),
- runs :func:`run_probe_config` against the resolved provider,
- optionally diffs against a baseline JSON report (``--compare``),
- emits the JSON report to stdout or ``--output`` path,
- returns the report's ``exit_code`` (0 / 1 / 2) — argparse-style 2
  for usage errors.

Test seam:

- ``registry`` kwarg of :func:`main` accepts a
  :class:`tests.fakes.FakeProviderRegistry` so no entry-point
  discovery is required.
- ``snapshotter`` kwarg accepts a stub returning a fixed
  ``TransportSnapshot`` so the runner's transport-stats branch is
  exercised without touching real transport modules.
- ``env_provider_lookup`` kwarg accepts a callable returning the
  desired provider name (or ``None``), avoiding env-var mutation
  entirely.

Every test marks ``@pytest.mark.unit`` (F8) and embeds a sabotage-
proof note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairix.quality.probe.config_cli import main
from kairix.quality.probe.config_runner import TransportSnapshot
from kairix.quality.probe.perf_runner import (
    OperationCallable,
    build_default_operations,
)
from tests.fakes import FakeProvider, FakeProviderRegistry


class _StubSnapshotter:
    """Returns a fixed ``TransportSnapshot`` so the runner doesn't poke real transport."""

    def snapshot(self) -> TransportSnapshot:
        return TransportSnapshot(
            coalesce_ratio=0.1,
            cache_hit_rate=0.5,
            pool_acquire_p50_ms=5.0,
        )


def _registry_with(name: str = "openai") -> FakeProviderRegistry:
    """Build a registry mapping ``name`` → ``FakeProvider``."""
    return FakeProviderRegistry({name: FakeProvider(name=name, vector=[0.1, 0.2, 0.3])})


def _short_argv(*extra: str) -> list[str]:
    """Build a fast-running argv slice with the smallest legal sample counts."""
    return ["--warm-samples", "1", "--concurrency", "1", "--repeated-samples", "1", *extra]


# ---------------------------------------------------------------------------
# Happy path — emits a JSON report to stdout and returns the report's exit code
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_emits_json_report_and_returns_report_exit_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A configured provider produces a JSON report on stdout and exit code 0.

    Sabotage-proof: removing the ``_emit_report(report, args.output)``
    call from main() leaves stdout empty; ``json.loads(captured.out)``
    raises ``JSONDecodeError`` and the test fails before reaching the
    exit-code assertion.
    """
    rc = main(
        _short_argv("--provider", "openai"),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload["provider"]["name"] == "openai"
    assert payload["status"] in {"healthy", "degraded", "unreachable"}


@pytest.mark.unit
def test_main_uses_env_lookup_when_provider_flag_absent() -> None:
    """No ``--provider`` flag → env_provider_lookup callable supplies the name.

    Sabotage-proof: removing the env-lookup fallback in
    ``_resolve_provider_name`` makes the lookup return ``None`` and
    the CLI returns exit code 2 (usage error) instead of the report
    exit code.
    """
    rc = main(
        _short_argv(),
        registry=_registry_with("anthropic"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: "anthropic",
    )

    # FakeProvider's healthcheck reports ok=True by default, so the
    # health verdict is healthy (exit 0).
    assert rc == 0


# ---------------------------------------------------------------------------
# Usage-error branches (exit code 2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_returns_2_when_no_provider_configured(capsys: pytest.CaptureFixture[str]) -> None:
    """Neither flag nor env supplies a provider → exit code 2 + stderr affordance.

    Sabotage-proof: removing the ``if not name: return None,
    _invalid_args(...)`` guard makes get_provider() crash on the
    ``None`` name and the rc isn't 2.
    """
    rc = main(
        _short_argv(),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    captured = capsys.readouterr()
    assert "no provider configured" in captured.err
    assert "fix:" in captured.err


@pytest.mark.unit
def test_main_returns_2_when_provider_not_registered(capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown provider name → ProviderNotRegistered → exit code 2.

    Sabotage-proof: removing the ``except ProviderNotRegistered`` in
    ``_resolve_provider`` propagates the exception; the test sees an
    uncaught exception instead of rc=2.
    """
    rc = main(
        _short_argv("--provider", "no_such_plugin"),
        registry=_registry_with("openai"),  # only 'openai' is registered
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    # The ProviderNotRegistered.__str__ surfaces in the actionable error.
    captured = capsys.readouterr()
    assert "no_such_plugin" in captured.err
    assert "fix:" in captured.err


@pytest.mark.unit
@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["--warm-samples", "0", "--concurrency", "1", "--repeated-samples", "1"], "warm-samples"),
        (["--warm-samples", "1", "--concurrency", "0", "--repeated-samples", "1"], "concurrency"),
        (["--warm-samples", "1", "--concurrency", "1", "--repeated-samples", "0"], "repeated-samples"),
        (["--degraded-p95-ms", "0"], "--degraded-p95-ms must be > 0"),
        (["--critical-p95-ms", "-1"], "--critical-p95-ms must be > 0"),
        # ``0`` (not just a negative) must be rejected — pins the ``<= 0``
        # boundary against a ``< 0`` weakening on the critical guard.
        (["--critical-p95-ms", "0"], "--critical-p95-ms must be > 0"),
        (["--degraded-p95-ms", "1000", "--critical-p95-ms", "500"], "must be >="),
    ],
)
def test_main_returns_2_when_sample_flag_below_minimum(
    capsys: pytest.CaptureFixture[str], argv: list[str], needle: str
) -> None:
    """Out-of-range sample/threshold flags → exit code 2 + actionable stderr.

    Sabotage-proof: dropping any of the ``if args.X < 1`` /
    ``args.degraded_p95_ms <= 0`` / ``args.critical_p95_ms <
    args.degraded_p95_ms`` guards lets the runner attempt the invalid
    value and either hangs or surfaces a different error class — the
    per-row ``needle`` substring then misses.
    """
    rc = main(
        [*argv, "--provider", "openai"],
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    captured = capsys.readouterr()
    assert needle in captured.err


@pytest.mark.unit
def test_equal_degraded_and_critical_thresholds_are_accepted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--critical-p95-ms == --degraded-p95-ms`` is valid, not a usage error.

    Critical is the "harsher or equal" threshold, so equal values are
    a legitimate operator choice (every degraded run is also critical).
    The run proceeds to a real verdict rather than exiting 2 for the
    cross-flag guard.

    Sabotage-proof (executed locally — see commit message): weaken the
    cross-flag guard from ``args.critical_p95_ms < args.degraded_p95_ms``
    to ``<=`` → equal values are rejected → ``rc != 2`` fails. Restored.
    """
    rc = main(
        _short_argv("--provider", "openai", "--degraded-p95-ms", "1000", "--critical-p95-ms", "1000"),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    captured = capsys.readouterr()
    assert rc != 2, f"equal thresholds should be accepted; got usage-error rc=2. stderr={captured.err!r}"
    assert "must be >=" not in captured.err, f"cross-flag guard fired on equal thresholds: {captured.err!r}"
    # The fast fake's ~0 ms latency sits under the 1000 ms threshold → healthy.
    assert rc == 0, f"expected healthy verdict (exit 0); got {rc}"


@pytest.mark.unit
def test_degraded_threshold_flag_flips_verdict_for_the_same_endpoint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--degraded-p95-ms`` tunes the verdict for one fixed endpoint.

    The default 1000 ms threshold classifies a ~50 ms endpoint as
    healthy (exit 0); lowering it to 25 ms re-classifies the SAME
    endpoint as degraded (exit 1). This is the operator tuning knob —
    no provider change, only the threshold.

    Sabotage-proof (executed locally — see commit message): drop the
    ``degraded_p95_ms=args.degraded_p95_ms`` wiring in ``main``'s
    ``run_probe_config`` call → the low-threshold run reverts to the
    1000 ms default → stays healthy (exit 0) → the ``rc_strict == 1``
    assertion fails. Restored.
    """
    registry = FakeProviderRegistry({"openai": FakeProvider(name="openai", dim=8, embed_latency_s=0.05)})

    # Default thresholds: ~50 ms p95 sits well under the 1000 ms default → healthy.
    rc_default = main(
        ["--warm-samples", "3", "--concurrency", "2", "--repeated-samples", "3", "--provider", "openai"],
        registry=registry,
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )
    default_payload = json.loads(capsys.readouterr().out)
    assert rc_default == 0, f"expected healthy (exit 0) at default threshold; got {rc_default}"
    assert default_payload["status"] == "healthy"

    # Strict threshold: the SAME endpoint is now degraded purely because the operator lowered the bar.
    rc_strict = main(
        [
            "--warm-samples",
            "3",
            "--concurrency",
            "2",
            "--repeated-samples",
            "3",
            "--provider",
            "openai",
            "--degraded-p95-ms",
            "25",
        ],
        registry=registry,
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )
    strict_payload = json.loads(capsys.readouterr().out)
    assert rc_strict == 1, f"expected degraded (exit 1) at 25 ms threshold; got {rc_strict}"
    assert strict_payload["status"] == "degraded"


# ---------------------------------------------------------------------------
# --output writes the report to a file instead of stdout
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_writes_report_to_output_path_when_supplied(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--output report.json`` writes JSON to file and emits nothing on stdout.

    Sabotage-proof: removing the ``output_path`` branch in
    ``_emit_report`` makes the function always print to stdout; the
    file would be empty and the JSON assertion fails.
    """
    out_path = tmp_path / "report.json"
    rc = main(
        _short_argv("--provider", "openai", "--output", str(out_path)),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["provider"]["name"] == "openai"


# ---------------------------------------------------------------------------
# --compare baseline JSON drives the comparison branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_attaches_comparison_when_baseline_supplied(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A valid baseline path populates ``report.comparison``.

    Sabotage-proof: removing the ``report = _attach_comparison(...)``
    line in main() leaves the report's ``comparison`` field as None;
    the assert on a populated comparison section fails.
    """
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "stage_latency_ms": {"cold": 100.0, "warm_sequential": 50.0},
                "collected_at": "2024-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        _short_argv("--provider", "openai", "--compare", str(baseline)),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 0
    assert payload.get("comparison") is not None


@pytest.mark.unit
def test_main_returns_2_when_baseline_path_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A nonexistent baseline path → exit code 2 + JSON still emitted.

    Sabotage-proof: removing the existence check makes the bare
    ``open()`` raise FileNotFoundError which doesn't pattern-match
    the rc=2 contract.
    """
    missing = tmp_path / "absent.json"

    rc = main(
        _short_argv("--provider", "openai", "--compare", str(missing)),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    captured = capsys.readouterr()
    # Per the implementation note, the report is still emitted on
    # stdout so the operator can see the verdict.
    assert captured.out.strip() != ""
    assert "does not exist" in captured.err


@pytest.mark.unit
def test_main_returns_2_when_baseline_is_malformed_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Malformed baseline JSON → exit code 2 + JSON still emitted.

    Sabotage-proof: removing the ``except (OSError, json.JSONDecodeError)``
    block lets the parse error propagate; the rc=2 contract is broken.
    """
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all", encoding="utf-8")

    rc = main(
        _short_argv("--provider", "openai", "--compare", str(bad)),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    captured = capsys.readouterr()
    assert "not valid JSON" in captured.err


@pytest.mark.unit
def test_main_handles_baseline_with_non_dict_stage_latency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A baseline whose ``stage_latency_ms`` isn't a dict short-circuits to {}.

    Sabotage-proof: dropping the ``if not isinstance(..., dict):
    baseline_stages = {}`` guard in ``_attach_comparison`` would
    surface AttributeError on the ``.items()`` call. The CLI would
    crash; the rc=0 contract fails.
    """
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "stage_latency_ms": "this is not a dict",
                "collected_at": "2024-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    rc = main(
        _short_argv("--provider", "openai", "--compare", str(baseline)),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    captured = capsys.readouterr()

    assert rc == 0
    payload = json.loads(captured.out)
    # Comparison still emitted (with empty baseline stages → no regressions).
    assert payload.get("comparison") is not None


# ---------------------------------------------------------------------------
# Unreachable provider — exit code mirrors EXIT_CODE_UNREACHABLE
# ---------------------------------------------------------------------------


class _UnreachableProvider:
    """``Provider`` that always raises ProviderUnreachable.

    Used to drive the ``if report.exit_code == EXIT_CODE_UNREACHABLE``
    branch in ``main`` — when the underlying probe cannot reach the
    endpoint, the runner marks the status as unreachable and the CLI
    returns exit code 2.
    """

    name = "broken"

    def embed_batch(self, _texts: list[str]) -> list[list[float]]:
        from kairix.providers import ProviderUnreachable

        raise ProviderUnreachable("simulated network failure")

    def chat(self, _messages: list[dict[str, object]], *, max_tokens: int = 800) -> str:
        del max_tokens
        return ""

    def dimension(self) -> int:
        return 1536

    def healthcheck(self) -> object:
        from kairix.providers import ProviderHealth

        return ProviderHealth(ok=False, endpoint="broken", error="ProviderUnreachable")


@pytest.mark.unit
def test_main_returns_unreachable_exit_code_when_provider_is_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unreachable provider surfaces exit code 2 via the EXIT_CODE_UNREACHABLE branch.

    Sabotage-proof: removing the ``if report.exit_code ==
    EXIT_CODE_UNREACHABLE: return EXIT_CODE_UNREACHABLE`` defensive
    branch in main() makes the CLI return the dataclass exit_code
    directly — which is also 2 today, but the explicit defensive
    branch is what the test pins. Mutating it to ``return 0`` makes
    this assertion fail.
    """
    registry = FakeProviderRegistry({"broken": _UnreachableProvider()})
    rc = main(
        _short_argv("--provider", "broken"),
        registry=registry,
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )

    assert rc == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "unreachable"


# ---------------------------------------------------------------------------
# --perf path — unit-scope coverage of the perf-dispatch branch in main()
# and the helpers it calls (_run_perf_path, _render_perf_human,
# _format_over_budget_suffix, _emit_perf_report).
#
# Each test calls main() directly with a tmp_path budgets file and
# injected operations dict so the suite runs sub-second and exercises
# every branch deterministically. The CLI is the public surface;
# F5 (no internal-name imports) is satisfied because we only import
# main + build_default_operations.
# ---------------------------------------------------------------------------


_PERF_BUDGETS: dict[str, dict[str, float]] = {
    "kairix_prep_vault_only": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_prep_facts_federated": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_per_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "kairix_ingest_chat_100_turn": {"p50_ms": 100.0, "p99_ms": 200.0},
    "fact_find_conflicts": {"p50_ms": 100.0, "p99_ms": 200.0},
    "federated_search_top_k_15": {"p50_ms": 100.0, "p99_ms": 200.0},
}


def _fast_op() -> None:
    """Sub-millisecond zero-arg op — every iteration sits under 100ms p50."""
    # Intentionally empty — timing measures the call-overhead floor.


def _ingest_one_turn_op(_i: int) -> None:
    """Per-iteration ingest stub — accepts the iteration index but is sub-ms."""
    # Intentionally empty — timing measures the call-overhead floor.


def _write_budgets(tmp_path: Path, payload: dict[str, dict[str, float]]) -> Path:
    """Serialise ``payload`` to ``tmp_path/budgets.json`` and return the path."""
    target = tmp_path / "budgets.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _wired_perf_operations() -> dict[str, OperationCallable]:
    """Wire every non-Cap-#5 op to a fast closure for happy-path coverage."""
    return build_default_operations(
        prep_vault_only=_fast_op,
        ingest_one_turn=_ingest_one_turn_op,
        ingest_100_turn=_fast_op,
        fact_find_conflicts=_fast_op,
    )


@pytest.mark.unit
def test_main_perf_happy_path_renders_human_pass_lines(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """All ops within budget → rc 0 + PASS markers in human output.

    Sabotage-proof: changing ``return 1 if report.any_violation else 0``
    in ``_run_perf_path`` to ``return 1`` collapses the happy path
    to a failing exit code; the rc=0 assertion fails.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    rc = main(
        ["--perf", "3", "--perf-budgets", str(budgets_path)],
        perf_operations=_wired_perf_operations(),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "kairix probe-config --perf" in captured.out
    assert "fact_find_conflicts" in captured.out
    assert "PASS" in captured.out


@pytest.mark.unit
def test_main_perf_violation_renders_fail_marker_and_returns_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A slow op flips rc to 1 + the FAIL marker + over-budget suffix.

    Sabotage-proof: removing the over-budget suffix branch in
    ``_format_over_budget_suffix`` strips ``p50 +Xms over`` from the
    FAIL line; the "+" substring check fails.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    ops = _wired_perf_operations()
    slow_latencies = [150.0, 160.0, 170.0, 180.0, 190.0]
    # Override one op to return canned over-budget latencies.
    ops["fact_find_conflicts"] = lambda _n: list(slow_latencies)
    rc = main(
        ["--perf", "5", "--perf-budgets", str(budgets_path)],
        perf_operations=ops,
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "fact_find_conflicts" in captured.out
    # The over-budget suffix surfaces the breach quantity.
    assert "over" in captured.out


@pytest.mark.unit
def test_main_perf_violation_p99_only_renders_p99_suffix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An op whose p50 is within budget but p99 is over emits only the p99 suffix.

    Sabotage-proof: removing the ``if over_p99 > 0:
    details.append(...)`` branch in ``_format_over_budget_suffix``
    drops the "p99 +" marker from output; the substring assertion
    fails. The p50 budget is set high enough that the p50 stays
    under it, while the p99 budget is tight enough that the highest
    latency breaches it.
    """
    # Latencies sorted ascending: [10, 20, 30, 40, 250].
    # nearest-rank p50 = 30, nearest-rank p99 = 250.
    latencies = [10.0, 20.0, 30.0, 40.0, 250.0]
    budgets = {"only_op": {"p50_ms": 100.0, "p99_ms": 100.0}}
    budgets_path = _write_budgets(tmp_path, budgets)
    rc = main(
        ["--perf", "5", "--perf-budgets", str(budgets_path)],
        perf_operations={"only_op": lambda _n: list(latencies)},
    )
    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.out
    assert "p99 +" in captured.out
    # p50 (30) is below the budget (100) so the p50 suffix is absent.
    assert "p50 +" not in captured.out


@pytest.mark.unit
def test_main_perf_json_envelope_emits_structured_payload(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--json`` switches output to a parseable JSON envelope on stdout.

    Sabotage-proof: dropping the ``if as_json:`` branch in
    ``_emit_perf_report`` always emits human text; ``json.loads(...)``
    raises JSONDecodeError and the test fails before any field check.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    rc = main(
        ["--perf", "2", "--perf-budgets", str(budgets_path), "--json"],
        perf_operations=_wired_perf_operations(),
    )
    assert rc == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["iterations"] == 2
    assert payload["any_violation"] is False
    assert isinstance(payload["results"], list)
    first = payload["results"][0]
    for key in ("operation", "p50_ms", "p99_ms", "budget_p50", "budget_p99", "within_budget"):
        assert key in first, f"JSON envelope missing key {key!r}"


@pytest.mark.unit
def test_main_perf_output_path_writes_json_to_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--perf --json --output FILE`` writes JSON to file, not stdout.

    Sabotage-proof: removing the ``if output_path:`` branch in
    ``_emit_perf_report`` makes the function always write to stdout;
    the file ends up empty and the JSON parse fails.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    out_path = tmp_path / "perf.json"
    rc = main(
        [
            "--perf",
            "2",
            "--perf-budgets",
            str(budgets_path),
            "--json",
            "--output",
            str(out_path),
        ],
        perf_operations=_wired_perf_operations(),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["iterations"] == 2


@pytest.mark.unit
def test_main_perf_output_path_writes_human_to_file(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--perf --output FILE`` (no ``--json``) writes the human table to file.

    Sabotage-proof: dropping the human branch of ``_emit_perf_report``
    leaves the file empty for human mode; the substring assertion
    on the table header fails.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    out_path = tmp_path / "perf.txt"
    rc = main(
        ["--perf", "2", "--perf-budgets", str(budgets_path), "--output", str(out_path)],
        perf_operations=_wired_perf_operations(),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    body = out_path.read_text(encoding="utf-8")
    assert "kairix probe-config --perf" in body


@pytest.mark.unit
def test_main_perf_skipped_ops_render_skip_diagnostic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cap #5-skipped ops surface ``capability not yet wired`` in human output.

    Sabotage-proof: dropping the ``if r.skipped: lines.append(...);
    continue`` branch in ``_render_perf_human`` makes skipped ops
    render as if they ran with p50=0/p99=0, hiding the operator-
    facing diagnostic.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    rc = main(
        ["--perf", "2", "--perf-budgets", str(budgets_path)],
        perf_operations=build_default_operations(),  # everything skipped
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "capability not yet wired" in captured.out
    assert "kairix_prep_facts_federated" in captured.out


@pytest.mark.unit
def test_main_perf_rejects_zero_iterations(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--perf 0`` → rc 2 + actionable stderr.

    Sabotage-proof: removing the ``if iterations < 1: return
    _invalid_args(...)`` guard in ``_run_perf_path`` lets the runner
    raise ValueError up the stack, breaking the operator-facing
    error contract.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    rc = main(
        ["--perf", "0", "--perf-budgets", str(budgets_path)],
        perf_operations=build_default_operations(),
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "perf iterations must be >= 1" in captured.err


@pytest.mark.unit
def test_main_perf_missing_budgets_file_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Nonexistent ``--perf-budgets`` → rc 2 + actionable ``fix:`` marker.

    Sabotage-proof: removing the ``if not budgets_path.exists(): return
    _invalid_args(...)`` guard makes ``load_budgets`` raise OSError
    further down with a less actionable message.
    """
    rc = main(
        ["--perf", "3", "--perf-budgets", str(tmp_path / "missing.json")],
        perf_operations=build_default_operations(),
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "perf budgets file not found" in captured.err
    assert "fix:" in captured.err


@pytest.mark.unit
def test_main_perf_malformed_budgets_file_returns_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A budgets file that fails JSON validation → rc 2 + diagnostic.

    Sabotage-proof: removing the ``except (ValueError, json.JSONDecodeError,
    OSError)`` block in ``_run_perf_path`` lets ``load_budgets`` raise
    upward; the rc=2 contract breaks.
    """
    bad = tmp_path / "bad.json"
    bad.write_text("not valid json", encoding="utf-8")
    rc = main(
        ["--perf", "3", "--perf-budgets", str(bad)],
        perf_operations=build_default_operations(),
    )
    assert rc == 2
    captured = capsys.readouterr()
    assert "perf budgets file malformed" in captured.err


@pytest.mark.unit
def test_main_perf_uses_default_operations_when_none_injected(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No ``perf_operations`` kwarg → main wires ``build_default_operations()``.

    Sabotage-proof: changing the default-fallback to
    ``perf_operations if perf_operations is not None else {}`` makes
    every op surface as the "no runner" skip — but the diagnostic
    still appears, so we instead pin the production behaviour: every
    budgets-key gets a result with the skip diagnostic shape.
    Replacing the fallback with ``None`` would crash at
    ``operations.get(op_name)`` and rc would not be 0.
    """
    budgets_path = _write_budgets(tmp_path, _PERF_BUDGETS)
    rc = main(["--perf", "1", "--perf-budgets", str(budgets_path)])
    assert rc == 0
    captured = capsys.readouterr()
    # Every op is skipped by default → human renderer shows the diagnostic
    # for every budgets entry.
    assert captured.out.count("capability not yet wired") >= len(_PERF_BUDGETS)


# ---------------------------------------------------------------------------
# _default_env_provider_lookup — direct unit cover of the default lookup
# path (covers config_cli.py lines 203 + 205).
#
# The default callable is exercised when callers DO NOT pass
# env_provider_lookup. Production goes through kairix.paths.provider_name
# which reads the env var. We don't set the env (F2), so the lookup
# returns None and main() falls through to the "no provider configured"
# branch. That's the canonical signal that the default lookup was
# invoked and didn't crash.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_main_uses_default_env_lookup_when_kwarg_omitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Omitting ``env_provider_lookup`` exercises the default lookup path.

    Sabotage-proof: deleting the ``if env_provider_lookup is None:
    env_provider_lookup = _default_env_provider_lookup`` block in
    main() makes the lookup callable be None, and the subsequent
    ``env_provider_lookup()`` call raises TypeError. The rc=2 contract
    breaks (we'd see an uncaught exception or a 1).

    Production reads ``KAIRIX_PROVIDER`` through
    ``kairix.paths.provider_name`` — without setting the env (F2
    forbids it in tests) the lookup returns None, the CLI emits the
    "no provider configured" error, and rc=2. That's the signal
    that the default lookup was constructed and invoked successfully.
    """
    rc = main(
        _short_argv(),
        registry=_registry_with("openai"),
        snapshotter=_StubSnapshotter(),
        # NOTE: env_provider_lookup omitted on purpose.
    )
    # Either:
    # - the env happens to be unset and we get rc=2 + "no provider configured"
    # - the env happens to be set and we get rc=2 + "ProviderNotRegistered"
    # Both confirm the default lookup callable was invoked successfully.
    assert rc == 2
    captured = capsys.readouterr()
    assert "fix:" in captured.err
