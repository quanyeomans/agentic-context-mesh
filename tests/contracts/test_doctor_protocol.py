"""Protocol-shape contract for :mod:`kairix.agents.onboarding.doctor`
(PR 1.5 / #420).

Pins the structural promises of :class:`SurfaceHealth`,
:class:`AgentHealth`, :class:`DoctorReport`, :func:`doctor_check_all`,
and :func:`doctor_check_agent`. The ``kairix doctor agent`` CLI + the
``tool_doctor_*`` MCP tools both consume these shapes; this contract
freezes them so the public surface cannot regress silently.
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from kairix.agents.onboarding.doctor import (
    AgentHealth,
    DoctorReport,
    SurfaceHealth,
    doctor_check_agent,
    doctor_check_all,
)

pytestmark = pytest.mark.contract


# Sabotage-proof (executed): removed `frozen=True` from the @dataclass
# decorator on SurfaceHealth → the FrozenInstanceError block did not
# trip when mutating `.exists`; assertion failed; restored.
def test_surface_health_is_frozen_dataclass() -> None:
    """``SurfaceHealth`` is a frozen dataclass — callers cache and
    compare doctor outcomes across runs and rely on immutability."""
    assert dataclasses.is_dataclass(SurfaceHealth)
    sample = SurfaceHealth(
        path=Path("/tmp/x"),
        label="memory",
        exists=True,
        file_count=3,
        most_recent_mtime=1_700_000_000.0,
        issues=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.exists = False  # type: ignore[misc]  # mutating frozen dc is the sabotage


# Sabotage-proof (executed): renamed `file_count` to `files` on
# SurfaceHealth → field-name set assertion failed; restored.
def test_surface_health_carries_expected_fields() -> None:
    """The six load-bearing fields callers read are present in name
    and in order — operators see them per-surface in the validation
    report."""
    field_names = [f.name for f in dataclasses.fields(SurfaceHealth)]
    assert field_names == [
        "path",
        "label",
        "exists",
        "file_count",
        "most_recent_mtime",
        "issues",
    ]


# Sabotage-proof (executed): dropped `frozen=True` from AgentHealth →
# the FrozenInstanceError block failed; restored.
def test_agent_health_is_frozen_dataclass() -> None:
    """``AgentHealth`` is a frozen dataclass — same caching + diff
    semantics as :class:`SurfaceHealth`."""
    assert dataclasses.is_dataclass(AgentHealth)
    sample = AgentHealth(
        name="agent-alpha",
        harness="claude-code",
        surfaces=(),
        overall="ok",
        issues=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.overall = "error"  # type: ignore[misc]  # mutating frozen dc is the sabotage


# Sabotage-proof (executed): reordered fields on AgentHealth → field-
# name list assertion failed; restored.
def test_agent_health_carries_expected_fields() -> None:
    """``AgentHealth`` exposes name, harness, surfaces, overall, issues
    in declared order — CLI + MCP envelopes index by name."""
    field_names = [f.name for f in dataclasses.fields(AgentHealth)]
    assert field_names == [
        "name",
        "harness",
        "surfaces",
        "overall",
        "issues",
    ]


# Sabotage-proof (executed): dropped `frozen=True` from DoctorReport →
# the FrozenInstanceError block failed; restored.
def test_doctor_report_is_frozen_dataclass() -> None:
    """``DoctorReport`` is a frozen dataclass — bulk outcomes survive
    round-trips through envelopes."""
    assert dataclasses.is_dataclass(DoctorReport)
    sample = DoctorReport(agents=(), overall="ok", summary_text="all clear")
    with pytest.raises(dataclasses.FrozenInstanceError):
        sample.overall = "error"  # type: ignore[misc]  # mutating frozen dc is the sabotage


# Sabotage-proof (executed): renamed `summary_text` to `summary` on
# DoctorReport → field name assertion failed; restored.
def test_doctor_report_carries_expected_fields() -> None:
    """``DoctorReport`` exposes agents, overall, summary_text — the
    operator-facing one-paragraph summary travels in summary_text."""
    field_names = [f.name for f in dataclasses.fields(DoctorReport)]
    assert field_names == ["agents", "overall", "summary_text"]


# Sabotage-proof (executed): changed doctor_check_all to return a
# list → isinstance assertion failed; restored.
def test_doctor_check_all_returns_doctor_report() -> None:
    """The function returns ``DoctorReport`` — never a list, iterator,
    or None — so callers can rely on the dataclass surface."""
    report = doctor_check_all(config={})
    assert isinstance(report, DoctorReport)


# Sabotage-proof (executed): renamed `config` kwarg to `cfg` on
# doctor_check_all → keyword bind assertion failed; restored.
def test_doctor_check_all_signature_pins_keyword_only_kwargs() -> None:
    """``doctor_check_all`` accepts ``config`` as a keyword-only kwarg
    — the CLI + MCP adapters depend on the parameter name."""
    sig = inspect.signature(doctor_check_all)
    assert "config" in sig.parameters


# Sabotage-proof (executed): made doctor_check_agent re-raise unknown-
# agent errors → the no-raise assertion failed because ValueError
# escaped; restored to the swallow-into-AgentHealth path.
def test_doctor_check_agent_never_raises_on_unknown(tmp_path: Path) -> None:
    """``doctor_check_agent`` returns an :class:`AgentHealth` even
    when the agent is unknown — callers branch on ``overall`` /
    ``issues`` rather than catching exceptions."""
    _ = tmp_path
    # No agent named "ghost" in config; the function MUST return an
    # AgentHealth carrying an actionable issues entry, NOT raise.
    health = doctor_check_agent("ghost", config={})
    assert isinstance(health, AgentHealth)
    assert health.name == "ghost"


# Sabotage-proof (executed): renamed the first positional param from
# `agent_name` to `name` → the inspect assertion failed; restored.
def test_doctor_check_agent_signature_pins_positional_name() -> None:
    """``doctor_check_agent`` takes the agent name as the first
    positional argument and ``config`` as keyword-only — MCP tool
    arguments map onto these names."""
    sig = inspect.signature(doctor_check_agent)
    first_name = next(iter(sig.parameters))
    assert first_name == "agent_name"
    assert "config" in sig.parameters
