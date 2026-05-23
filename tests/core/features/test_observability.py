"""Unit tests for the feature-flag observability hooks.

Covers first-activation logging (one INFO per process per flag), the
counter-sink seam, and the test-only reset helper.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from kairix.core.features.observability import (
    emit_activation_counter,
    log_first_activation,
    reset_observability_state,
    set_counter_sink,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_observability() -> Iterator[None]:
    """Pristine state per test."""
    reset_observability_state()
    yield
    reset_observability_state()


def test_log_first_activation_emits_one_info_per_flag(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """First call → one INFO line. Subsequent calls for the same flag
    are silent. Tests the per-process dedupe contract.
    """
    with caplog.at_level(logging.INFO, logger="kairix.core.features.observability"):
        log_first_activation("canary", effective=True, source="default")
        log_first_activation("canary", effective=True, source="default")
        log_first_activation("canary", effective=True, source="default")

    activation_records = [r for r in caplog.records if "feature_flag.activation" in r.getMessage()]
    assert len(activation_records) == 1, (
        f"expected one INFO record per (process, flag) pair; got {len(activation_records)}"
    )


def test_log_first_activation_emits_per_distinct_flag(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Distinct flags each get their own first-activation log."""
    with caplog.at_level(logging.INFO, logger="kairix.core.features.observability"):
        log_first_activation("alpha", effective=True, source="env")
        log_first_activation("beta", effective=False, source="default")

    activation_records = [r for r in caplog.records if "feature_flag.activation" in r.getMessage()]
    assert len(activation_records) == 2


def test_log_first_activation_records_source_label(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log line must carry the source label so operators can see
    which layer of §3.4 won.
    """
    with caplog.at_level(logging.INFO, logger="kairix.core.features.observability"):
        log_first_activation("canary", effective=True, source="config")

    assert any("source='config'" in r.getMessage() for r in caplog.records)


def test_emit_activation_counter_defaults_to_noop() -> None:
    """The default counter sink is a no-op; calls must not raise."""
    # No assertion needed — the contract is "must not raise". The
    # success signal is the absence of an exception.
    emit_activation_counter("canary", True)


def test_set_counter_sink_routes_subsequent_emits() -> None:
    """Installing a sink → emits forward into it."""
    captured: list[tuple[str, bool]] = []

    def capture(name: str, effective: bool) -> None:
        captured.append((name, effective))

    set_counter_sink(capture)
    try:
        emit_activation_counter("canary", True)
        emit_activation_counter("beta", False)
    finally:
        # Restore the no-op sink so other tests aren't polluted.
        from kairix.core.features.observability import noop_counter

        set_counter_sink(noop_counter)

    assert captured == [("canary", True), ("beta", False)]
