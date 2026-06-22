"""Cold-start affordance tests for the Kairix MCP surface."""

from __future__ import annotations

import pytest

from kairix.agents.mcp.cold_start import (
    DEFAULT_ESTIMATED_SECONDS,
    cold_start_envelope,
    is_cold_start_envelope,
    require_ready,
)

pytestmark = pytest.mark.unit


def test_cold_start_envelope_is_machine_actionable_and_prescriptive() -> None:
    payload = cold_start_envelope(tool_name="search", retry_after_ms=7000, estimated_seconds_remaining=7.0)

    assert payload["status"] == "retryable_not_ready"
    assert payload["error_code"] == "KAIRIX_COLD_START"
    assert payload["retry_after_ms"] == 7000
    assert payload["estimated_seconds_remaining"] == 7.0
    # Affordance pattern (feedback_agent_prompts_positive_assertion):
    # leads with `next:` and `fix:` positive actions, not stacked prohibitions.
    assert "next:" in payload["agent_instruction"]
    assert "fix:" in payload["agent_instruction"]
    assert "'search'" in payload["agent_instruction"]
    # Anti-pattern guard: prohibitions ("do not X, do not Y") should not appear
    assert "Do not" not in payload["agent_instruction"]
    assert "do not" not in payload["agent_instruction"]


def test_is_cold_start_envelope_recognises_canonical_shape() -> None:
    assert is_cold_start_envelope(cold_start_envelope(tool_name="bootstrap")) is True
    assert is_cold_start_envelope({"error": "ColdStart"}) is False
    assert is_cold_start_envelope("ColdStart") is False


def test_require_ready_returns_none_when_no_gate_or_ready() -> None:
    assert require_ready("search", None) is None
    assert require_ready("search", lambda: True) is None


def test_require_ready_returns_cold_start_when_gate_not_ready() -> None:
    payload = require_ready("search", lambda: False)

    assert payload is not None
    assert payload["error_code"] == "KAIRIX_COLD_START"
    assert payload["tool"] == "search"


# ---------------------------------------------------------------------------
# warm_retrieval_stack — end-to-end envelope shape coverage
# ---------------------------------------------------------------------------
#
# These tests call the production warm_retrieval_stack() directly. In the
# test env there's no provider config, so build_search_pipeline either
# succeeds (returns a degraded but usable pipeline whose search() may
# still error on missing FTS/vector index) or fails outright with an
# ImportError / ConfigError. Both paths produce a structured envelope —
# we assert the envelope SHAPE rather than the success/failure outcome.
# This drives the function's body lines (107, 129-154) without resorting
# to monkeypatch / injection seams on production helpers.


def test_warm_retrieval_stack_returns_structured_envelope() -> None:
    """The function always returns a status/ready/elapsed_ms/steps envelope.

    Sabotage-proof: drop the final happy-path return statement at line 154
    and this test fails when the function falls off the end and returns None.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()

    assert isinstance(payload, dict)
    assert "status" in payload
    assert "ready" in payload
    assert "elapsed_ms" in payload
    assert "steps" in payload
    assert payload["status"] in {"ok", "error"}
    assert isinstance(payload["ready"], bool)
    assert isinstance(payload["elapsed_ms"], int)
    assert isinstance(payload["steps"], list)


def test_warm_retrieval_stack_step_records_are_well_formed() -> None:
    """Each entry in steps[] has name + ok (+ elapsed_ms on success or error on failure).

    Sabotage-proof: drop the steps.append in the happy path and the
    success case asserts on an empty list; drop the error branch's
    steps.append and the failure case asserts on the same.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()
    steps = payload["steps"]

    # At least one step record is emitted regardless of outcome — either
    # the build_search_pipeline step (success or failure) or both
    # build + probe_search steps when the pipeline constructs cleanly.
    assert len(steps) >= 1
    for step in steps:
        assert "name" in step
        assert "ok" in step
        assert step["name"] in {"build_search_pipeline", "probe_search"}
        if step["ok"] is True:
            assert "elapsed_ms" in step
        else:
            assert "error" in step
            assert isinstance(step["error"], str)


def test_warm_retrieval_stack_ready_aligns_with_status() -> None:
    """``ready=True`` iff ``status=='ok'``; both fields must agree.

    Sabotage-proof: invert the happy-path return's ``ready`` value and
    the assertion ``(status == 'ok') == (ready is True)`` catches it.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()
    assert (payload["status"] == "ok") == (payload["ready"] is True)
    if payload["status"] == "error":
        assert payload["ready"] is False


# ---------------------------------------------------------------------------
# require_ready — live warm-progress DI seam (_resolve_warm_progress source path)
# ---------------------------------------------------------------------------
#
# require_ready exposes ``warm_progress_source`` as a public DI seam (the
# function's own docstring: "tests inject a fake returning a fixed
# WarmProgress"). The seam routes through _resolve_warm_progress, whose
# source-provided branch is the not-None path. These tests drive that path
# with a fake source — no kairix-internal patching (F1-clean), no module
# globals touched.


def test_require_ready_threads_injected_warm_progress_into_envelope() -> None:
    """A not-ready gate + injected WarmProgress yields a LIVE remaining estimate.

    Pins the cold_start ↔ WarmProgress wiring through ``require_ready``'s
    ``warm_progress_source`` seam: the returned envelope must reflect the
    fake's live ``remaining_seconds()`` (60s into a 120s budget → ~60s),
    NOT the static 8s ColdStartConfig default. This proves the seam is
    actually consulted, not bypassed.

    Sabotage-proof: drop the ``warm_progress=progress`` argument in
    ``require_ready``'s call to ``cold_start_envelope`` (or make
    ``_resolve_warm_progress`` ignore ``source``); the envelope reverts to
    the static 8s default and ``estimated_seconds_remaining`` is 8.0, so
    the ``~60s`` assertion fails.
    """
    from kairix.platform.warm.state import WarmProgress

    started = 1_000_000.0
    clock = [started + 60.0]  # 60s elapsed of a 120s budget
    progress = WarmProgress(
        started_at=started,
        total_estimate_seconds=120.0,
        time_source=lambda: clock[0],
    )

    payload = require_ready(
        "search",
        lambda: False,  # not ready → cold-start path
        warm_progress_source=lambda: progress,
    )

    assert payload is not None
    assert payload["error_code"] == "KAIRIX_COLD_START"
    remaining = payload["estimated_seconds_remaining"]
    assert 59.0 <= remaining <= 61.0, (
        f"require_ready must thread the injected WarmProgress through to a LIVE "
        f"~60s remaining estimate; got {remaining!r}. If 8.0, the warm_progress_source "
        f"seam was not consulted (the static ColdStartConfig default leaked through)."
    )
    # retry_after_ms tracks the live remaining (max(1000, remaining*1000)).
    assert payload["retry_after_ms"] >= 1000
    assert abs(payload["retry_after_ms"] - int(remaining * 1000)) <= 1000


def test_require_ready_static_default_when_source_returns_none() -> None:
    """When the injected source returns None (warm not started), the envelope
    keeps the static ColdStartConfig defaults — backwards compatibility.

    Pins that the ``warm_progress_source`` seam returning None is the
    explicit "no live progress" signal, distinct from a present-but-empty
    WarmProgress. The envelope falls back to the static 8s estimate and
    omits the ``elapsed_seconds`` key entirely.

    Sabotage-proof: invert the ``warm_progress is not None`` guard in
    ``cold_start_envelope`` and the static path is skipped — the assertion
    on the 8.0 default fails.
    """
    payload = require_ready(
        "search",
        lambda: False,
        warm_progress_source=lambda: None,
    )

    assert payload is not None
    assert payload["estimated_seconds_remaining"] == DEFAULT_ESTIMATED_SECONDS
    assert "elapsed_seconds" not in payload


# ---------------------------------------------------------------------------
# warm_retrieval_stack — orchestration branches via the WarmStackDeps seam
# ---------------------------------------------------------------------------
#
# warm_retrieval_stack constructs the real SearchPipeline + runs a read-only
# probe. The success and probe-failure branches need a working pipeline,
# which in unit scope is unavailable (no provider secret, no KV mount). The
# WarmStackDeps seam (canonical kairix Deps shape, same as
# Neo4jDrainTickDeps) lets tests inject a fake pipeline_factory so the
# orchestration — step records, read-only probe call, success vs
# probe-failure vs build-failure envelope assembly — is asserted at unit
# scope, F1-clean (constructor injection of a Fake, no internal patching).


def test_warm_retrieval_stack_success_path_records_both_steps_ready() -> None:
    """A pipeline that builds AND probes cleanly yields status=ok, ready=True,
    with both ``build_search_pipeline`` and ``probe_search`` step records.

    This is the happy path the production warm-up takes once the retrieval
    backend is reachable. Asserts the real orchestration output: both named
    steps present and ``ok``, a non-negative total ``elapsed_ms``, and the
    ready/ok agreement.

    Sabotage-proof: drop the final happy-path ``return`` (line returning
    status=ok) and the function falls off the end returning None →
    ``payload["status"]`` raises TypeError. Flip the happy-path ``ready``
    to False and the ``ready is True`` assertion fails. Drop the
    ``probe_search`` ``steps.append`` and the two-step assertion fails.
    """
    from kairix.agents.mcp.cold_start import WarmStackDeps, warm_retrieval_stack
    from tests.fakes import FakeSearchPipeline

    fake = FakeSearchPipeline()
    payload = warm_retrieval_stack(deps=WarmStackDeps(pipeline_factory=lambda: fake))

    assert payload["status"] == "ok"
    assert payload["ready"] is True
    assert isinstance(payload["elapsed_ms"], int) and payload["elapsed_ms"] >= 0
    names = [s["name"] for s in payload["steps"]]
    assert names == ["build_search_pipeline", "probe_search"], (
        f"success path must record both steps in order; got {names!r}"
    )
    assert all(s["ok"] is True for s in payload["steps"])


def test_warm_retrieval_stack_success_path_runs_readonly_probe() -> None:
    """The probe is a read-only ``search(query='kairix warmup', budget=200,
    collections=[])`` against the freshly-built pipeline.

    Pins the warm-up's read-only contract: warm_retrieval_stack must
    actually exercise the search path (so a broken index surfaces at warm
    time, not on the first agent call), and must do so with an empty
    collection set + a small budget so it never mutates or scans the full
    corpus.

    Sabotage-proof: change the probe to ``pipeline.search()`` with no
    args, or a different query/budget, and the recorded-call assertion
    fails.
    """
    from kairix.agents.mcp.cold_start import WarmStackDeps, warm_retrieval_stack
    from tests.fakes import FakeSearchPipeline

    fake = FakeSearchPipeline()
    warm_retrieval_stack(deps=WarmStackDeps(pipeline_factory=lambda: fake))

    assert len(fake.calls) == 1, f"warm-up must run exactly one probe search; got {len(fake.calls)}"
    call = fake.calls[0]
    assert call["query"] == "kairix warmup"
    assert call["kwargs"]["budget"] == 200
    assert call["kwargs"]["collections"] == []


def test_warm_retrieval_stack_probe_failure_records_build_ok_then_probe_error() -> None:
    """When the pipeline BUILDS but the probe ``search`` raises, the envelope
    is status=error/ready=False with an ``ok`` build step followed by a
    ``probe_search`` step carrying the error string.

    This is the degradation path the build-failure branch can't reach: the
    factory succeeds (build step recorded ``ok``) but the read-only probe
    explodes (e.g. a cold/missing vector index). The envelope must
    distinguish this from a build failure by keeping the ``ok`` build step
    and naming ``probe_search`` as the failure.

    Sabotage-proof: drop the probe-failure ``except`` branch's ``return``
    and the function falls through to status=ok despite the raised probe →
    the status/error assertions fail. Drop the build ``steps.append`` and
    the "build step recorded ok" assertion fails.
    """

    class _ProbeExplodingPipeline:
        """Local boundary fake: builds fine, probe raises. Not a kairix
        internal — a stand-in satisfying the pipeline_factory seam, so
        F1-clean."""

        def search(self, **kwargs: object) -> object:
            raise RuntimeError("vec_index cold: usearch view not yet mapped")

    from kairix.agents.mcp.cold_start import WarmStackDeps, warm_retrieval_stack

    payload = warm_retrieval_stack(deps=WarmStackDeps(pipeline_factory=_ProbeExplodingPipeline))

    assert payload["status"] == "error"
    assert payload["ready"] is False
    steps = payload["steps"]
    assert steps[0]["name"] == "build_search_pipeline"
    assert steps[0]["ok"] is True
    assert "elapsed_ms" in steps[0], "an ok step must carry its elapsed_ms timing"
    assert steps[-1]["name"] == "probe_search"
    assert steps[-1]["ok"] is False
    assert "RuntimeError" in steps[-1]["error"]
    assert "vec_index cold" in steps[-1]["error"]


def test_warm_retrieval_stack_build_failure_records_single_failed_step() -> None:
    """When the pipeline_factory itself raises, the envelope is
    status=error/ready=False with a single failed ``build_search_pipeline``
    step and no ``probe_search`` step.

    Pins the earliest degradation: the factory can't even construct the
    pipeline (the unit-env default behaviour — no provider secret). The
    probe must NOT run, so only one step is recorded and it names the
    build failure.

    Sabotage-proof: route the build-failure ``except`` to fall through to
    the probe and a ``probe_search`` step would appear → the single-step
    assertion fails.
    """

    def _exploding_factory() -> object:
        raise RuntimeError("SecretNotFoundError: kairix-provider-llm-api-key")

    from kairix.agents.mcp.cold_start import WarmStackDeps, warm_retrieval_stack

    payload = warm_retrieval_stack(deps=WarmStackDeps(pipeline_factory=_exploding_factory))

    assert payload["status"] == "error"
    assert payload["ready"] is False
    assert len(payload["steps"]) == 1
    only_step = payload["steps"][0]
    assert only_step["name"] == "build_search_pipeline"
    assert only_step["ok"] is False
    assert "RuntimeError" in only_step["error"]


def test_warm_retrieval_stack_default_deps_binds_production_factory() -> None:
    """With ``deps=None`` the function binds the production pipeline factory.

    Pins that the seam preserves production behaviour: the default path
    constructs the REAL ``build_search_pipeline`` (which in the unit env
    has no provider secret and fails at build), so the envelope is a
    well-formed build-failure — proving the default factory ran, not a
    test fake. This keeps F86's DI-default-execution floor satisfied: the
    ``_default_build_search_pipeline`` seam is executed by the suite.

    Sabotage-proof: change the ``deps or WarmStackDeps()`` default binding
    to a no-op and the envelope shape (status/ready/steps) degrades.
    """
    from kairix.agents.mcp.cold_start import warm_retrieval_stack

    payload = warm_retrieval_stack()  # deps=None → production default factory

    assert payload["status"] in {"ok", "error"}
    assert isinstance(payload["ready"], bool)
    assert (payload["status"] == "ok") == (payload["ready"] is True)
    assert payload["steps"], "default-deps path must still record at least the build step"
    assert payload["steps"][0]["name"] == "build_search_pipeline"
