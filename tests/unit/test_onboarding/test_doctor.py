"""Unit tests for :mod:`kairix.agents.onboarding.doctor` (PR 1.5 / #420).

The doctor walks every configured agent scope and validates each
surface against disk state. These tests pin the per-rule mapping
(missing dir → "error", stale file → "warn", ambiguous overlap →
"ambiguous" on both surfaces) so future contributors can refactor
the internals without changing the operator-facing contract.

Disk IO never raises — every failure mode collapses to a
``SurfaceHealth.issues`` entry the operator can read and act on.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from kairix.agents.onboarding.doctor import (
    AgentHealth,
    DoctorReport,
    SurfaceHealth,
    doctor_check_agent,
    doctor_check_all,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_config(
    name: str,
    surface_path: Path,
    *,
    glob: str = "**/*.md",
    label: str = "memory",
    harness: str = "claude-code",
) -> dict[str, object]:
    """Build a one-agent ``agents:`` config block for a doctor test."""
    return {
        "agents": {
            name: {
                "harness": harness,
                "surfaces": [
                    {"path": str(surface_path), "glob": glob, "label": label},
                ],
            },
        },
    }


def _seed_recent_md_files(directory: Path, count: int) -> None:
    """Drop ``count`` .md files into ``directory``; mtime = now."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"note-{i}.md").write_text(f"# note {i}\n")


def _age_file_by_days(path: Path, days: int) -> None:
    """Backdate a file's mtime by ``days``."""
    target = time.time() - (days * 86_400)
    os.utime(path, (target, target))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): hardcoded overall="error" in
# doctor_check_all → the "ok" assertion failed; restored.
def test_populated_recent_scope_returns_ok(tmp_path: Path) -> None:
    """Every surface populated with recent .md files → overall="ok"."""
    surface = tmp_path / "agent-alpha"
    _seed_recent_md_files(surface, 3)
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    assert report.overall == "ok"
    assert len(report.agents) == 1
    assert report.agents[0].overall == "ok"


# ---------------------------------------------------------------------------
# Path missing
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made the missing-dir branch return
# exists=True → both the "error" assertion and the issue-message
# assertion failed; restored.
def test_missing_surface_dir_flags_path_missing(tmp_path: Path) -> None:
    """Surface dir does not exist → overall="error" and surface
    carries "path missing" issue naming `kairix onboard agent`.

    Also pins the write-access probe's non-mutating contract (PLA-259): a
    missing surface reports writable=False and the doctor must NOT create it.

    Sabotage-proof (executed): flipped ``create=False`` to True in
    ``_probe_surface`` → the missing dir was created and probed writable, so
    ``not surface.exists()`` and ``writable is False`` both failed; restored.
    """
    surface = tmp_path / "no-such-dir"
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    assert report.overall == "error"
    assert report.agents[0].overall == "error"
    surface_health = report.agents[0].surfaces[0]
    assert not surface_health.exists
    assert surface_health.writable is False  # a missing surface is not writable
    assert not surface.exists(), "doctor must not create the surface while probing it"
    joined_issues = " ".join(surface_health.issues)
    assert "path missing" in joined_issues
    assert "kairix onboard agent" in joined_issues


# ---------------------------------------------------------------------------
# No files matching glob
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made the empty-dir branch return
# overall="ok" → the "warn" assertion failed; restored.
def test_surface_with_no_md_files_flags_warn(tmp_path: Path) -> None:
    """Dir exists but no .md files matching glob → overall="warn"
    with "no files matching glob" issue."""
    surface = tmp_path / "agent-alpha"
    surface.mkdir()
    # Drop a non-matching file so the dir is non-empty but glob fails.
    (surface / "ignore.txt").write_text("not markdown\n")
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    assert report.overall == "warn"
    assert report.agents[0].overall == "warn"
    joined_issues = " ".join(report.agents[0].surfaces[0].issues)
    assert "no files matching glob" in joined_issues


# ---------------------------------------------------------------------------
# Stale
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): forced staleness threshold to 365 days
# → the 45-day assertion failed (no stale issue emitted); restored
# to the 30-day threshold.
def test_old_files_flag_stale_warning(tmp_path: Path) -> None:
    """Most recent .md file > 30 days old → overall="warn" with
    actionable "stale — most recent file is N days old" message."""
    surface = tmp_path / "agent-alpha"
    surface.mkdir()
    old_file = surface / "old.md"
    old_file.write_text("# old\n")
    _age_file_by_days(old_file, 45)
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    assert report.overall == "warn"
    joined_issues = " ".join(report.agents[0].surfaces[0].issues)
    assert "stale" in joined_issues
    assert "45" in joined_issues


# ---------------------------------------------------------------------------
# Ambiguous overlap
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): dropped the overlap detection pass → the
# "ambiguous" assertion failed because neither surface flagged it;
# restored.
def test_overlapping_globs_flag_both_surfaces_ambiguous(tmp_path: Path) -> None:
    """When two agents' surfaces match the same file, BOTH surfaces
    flag an "ambiguous" issue identifying the conflict and the
    overlapping path."""
    shared = tmp_path / "shared"
    _seed_recent_md_files(shared, 1)

    config: dict[str, object] = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [{"path": str(shared), "glob": "**/*.md", "label": "memory"}],
            },
            "agent-beta": {
                "harness": "generic",
                "surfaces": [{"path": str(shared), "glob": "**/*.md", "label": "memory"}],
            },
        },
    }
    report = doctor_check_all(config=config)
    by_name = {a.name: a for a in report.agents}
    alpha_issues = " ".join(by_name["agent-alpha"].surfaces[0].issues)
    beta_issues = " ".join(by_name["agent-beta"].surfaces[0].issues)
    assert "ambiguous" in alpha_issues
    assert "ambiguous" in beta_issues
    # Both messages mention the other agent so the operator can locate
    # the colliding config block.
    assert "agent-beta" in alpha_issues
    assert "agent-alpha" in beta_issues


# ---------------------------------------------------------------------------
# Fallback (no explicit config, but agent_defaults synthesises a scope)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made doctor_check_agent ignore the
# fallback path and return overall="ok" → the "warn" assertion
# failed; restored.
def test_fallback_to_defaults_flags_warn_with_onboard_suggestion(
    tmp_path: Path,
) -> None:
    """No explicit agents.<name> entry but agent_defaults.memory_root
    present → overall="warn" with the standard onboard suggestion."""
    memory_root = tmp_path / "memory"
    agent_dir = memory_root / "agent-alpha"
    _seed_recent_md_files(agent_dir, 2)
    config: dict[str, object] = {
        "agent_defaults": {
            "memory_root": str(memory_root),
            "glob": "**/*.md",
        },
    }
    health = doctor_check_agent("agent-alpha", config=config)
    assert health.overall == "warn"
    joined_agent_issues = " ".join(health.issues)
    assert "kairix onboard agent" in joined_agent_issues
    assert "no explicit config" in joined_agent_issues


# Sabotage-proof (executed): made doctor_check_agent raise on
# completely-unknown agents → the no-raise contract test failed;
# restored to the swallow-into-AgentHealth path.
def test_completely_unknown_agent_returns_error_agent_health(
    tmp_path: Path,
) -> None:
    """No explicit config AND no agent_defaults → return an
    AgentHealth with overall="error" — never raise."""
    _ = tmp_path
    health = doctor_check_agent("ghost", config={})
    assert isinstance(health, AgentHealth)
    assert health.overall == "error"
    joined = " ".join(health.issues)
    assert "ghost" in joined


# Sabotage-proof (executed): made doctor_check_all return overall="ok"
# even with empty agents → the "ok"-on-empty assertion still passes
# (an empty config has zero agents and zero issues); confirmed by
# inverting the assertion to fail; restored.
def test_empty_config_returns_ok_with_zero_agents() -> None:
    """Empty agents config → DoctorReport with overall="ok" and no
    agents — operator sees "no agents configured" via summary_text."""
    report = doctor_check_all(config={})
    assert report.overall == "ok"
    assert report.agents == ()
    # Operator-facing summary must still be present so the CLI has
    # something to print.
    assert isinstance(report.summary_text, str)


# ---------------------------------------------------------------------------
# doctor_check_agent on an explicitly configured agent (happy path)
# ---------------------------------------------------------------------------


# Sabotage-proof (executed): made doctor_check_agent ignore the
# explicit-config path and always fall through to fallback → the
# overall="ok" assertion failed; restored.
def test_doctor_check_agent_explicit_config_recent_files_ok(
    tmp_path: Path,
) -> None:
    """When the agent has explicit config and surfaces are populated
    + recent, doctor_check_agent returns overall="ok"."""
    surface = tmp_path / "agent-alpha"
    _seed_recent_md_files(surface, 2)
    config = _make_agent_config("agent-alpha", surface)
    health = doctor_check_agent("agent-alpha", config=config)
    assert health.overall == "ok"
    assert health.name == "agent-alpha"
    assert health.harness == "claude-code"


# Sabotage-proof (executed): forced the summary text to "" → the
# substring assertion failed because the operator-readable summary
# disappeared; restored.
def test_doctor_report_summary_text_carries_agent_counts(
    tmp_path: Path,
) -> None:
    """``summary_text`` mentions the agent count + overall status so
    the CLI's default mode has a human-readable line to print."""
    surface = tmp_path / "agent-alpha"
    _seed_recent_md_files(surface, 1)
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    assert "1" in report.summary_text
    assert "ok" in report.summary_text


# Sabotage-proof (executed): made the missing-dir branch raise instead
# of returning the SurfaceHealth → the assertion failed because the
# function escaped; restored to the swallow path.
def test_missing_dir_does_not_raise_in_doctor_check_agent(tmp_path: Path) -> None:
    """``doctor_check_agent`` never raises on missing disk paths."""
    surface = tmp_path / "no-such-dir"
    config = _make_agent_config("agent-alpha", surface)
    # Must not raise.
    health = doctor_check_agent("agent-alpha", config=config)
    assert isinstance(health, AgentHealth)
    assert health.overall == "error"


# Sabotage-proof (executed): made SurfaceHealth always report
# file_count=0 when the dir was missing → the assertion still passed
# (correct branch); inverted to confirm; restored.
def test_surface_health_file_count_zero_when_missing_dir(tmp_path: Path) -> None:
    """``file_count == 0`` and ``most_recent_mtime is None`` when the
    surface dir does not exist."""
    surface = tmp_path / "ghost"
    config = _make_agent_config("agent-alpha", surface)
    report = doctor_check_all(config=config)
    sh = report.agents[0].surfaces[0]
    assert isinstance(sh, SurfaceHealth)
    assert sh.file_count == 0
    assert sh.most_recent_mtime is None


# Sabotage-proof (executed): made doctor_check_all skip the
# config=None branch → calling without config raised TypeError;
# restored the None default.
def test_doctor_check_all_accepts_none_config() -> None:
    """``doctor_check_all(config=None)`` returns a DoctorReport — the
    CLI passes None when the operator has no config file."""
    report = doctor_check_all(config=None)
    assert isinstance(report, DoctorReport)
    assert report.agents == ()


# Sabotage-proof (executed): made doctor_check_agent accept config=None
# but raise on it → the None assertion failed; restored to graceful
# error AgentHealth.
def test_doctor_check_agent_accepts_none_config() -> None:
    """``doctor_check_agent(name, config=None)`` returns an
    AgentHealth — does not raise."""
    health = doctor_check_agent("ghost", config=None)
    assert isinstance(health, AgentHealth)
    assert health.overall == "error"


# Sabotage-proof (executed): removed the `if a_name == b_name continue`
# guard → a single agent with two overlapping surfaces (same path)
# self-collided and emitted spurious ambiguity issues; restored.
def test_two_surfaces_within_one_agent_do_not_self_collide(tmp_path: Path) -> None:
    """When one agent declares two surfaces pointing at the same dir,
    the overlap pass does NOT flag the agent as ambiguous with
    itself."""
    shared = tmp_path / "shared"
    _seed_recent_md_files(shared, 1)
    config: dict[str, object] = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [
                    {"path": str(shared), "glob": "**/*.md", "label": "memory"},
                    {"path": str(shared), "glob": "**/*.md", "label": "workspace"},
                ],
            },
        },
    }
    report = doctor_check_all(config=config)
    issues = " ".join(s for sh in report.agents[0].surfaces for s in sh.issues)
    assert "ambiguous" not in issues


# Sabotage-proof (executed): removed the `if not shared continue`
# guard in the overlap pass → unrelated agents with disjoint surfaces
# tripped the ambiguity path; restored.
def test_disjoint_surfaces_across_agents_do_not_overlap(tmp_path: Path) -> None:
    """Two agents with surfaces in unrelated directories must NOT be
    flagged as ambiguous."""
    a = tmp_path / "agent-alpha"
    b = tmp_path / "agent-beta"
    _seed_recent_md_files(a, 1)
    _seed_recent_md_files(b, 1)
    config: dict[str, object] = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [{"path": str(a), "glob": "**/*.md", "label": "memory"}],
            },
            "agent-beta": {
                "harness": "generic",
                "surfaces": [{"path": str(b), "glob": "**/*.md", "label": "memory"}],
            },
        },
    }
    report = doctor_check_all(config=config)
    issues = " ".join(s for agent in report.agents for sh in agent.surfaces for s in sh.issues)
    assert "ambiguous" not in issues
    assert report.overall == "ok"


# Sabotage-proof (executed): removed the load_agent_scopes exception
# wrap → a malformed agents block raised ValueError out of
# doctor_check_all; restored to swallow + warn + empty.
def test_malformed_config_collapses_to_empty_report() -> None:
    """A malformed agents block (e.g. a string where a dict is
    expected) collapses to an empty DoctorReport — never raises."""
    config: dict[str, object] = {"agents": "not a dict"}
    report = doctor_check_all(config=config)
    assert isinstance(report, DoctorReport)
    assert report.agents == ()


# Sabotage-proof (executed): made the rebuilt-overall path skip the
# "promote to warn" branch on overlap → an overlap left the agent at
# overall="ok"; the assertion below failed; restored.
def test_overlap_promotes_overall_to_warn(tmp_path: Path) -> None:
    """When the only issue on a surface is an overlap, the agent's
    overall MUST step from ok to warn so operators see the flag."""
    shared = tmp_path / "shared"
    _seed_recent_md_files(shared, 1)
    config: dict[str, object] = {
        "agents": {
            "agent-alpha": {
                "harness": "claude-code",
                "surfaces": [{"path": str(shared), "glob": "**/*.md", "label": "memory"}],
            },
            "agent-beta": {
                "harness": "generic",
                "surfaces": [{"path": str(shared), "glob": "**/*.md", "label": "memory"}],
            },
        },
    }
    report = doctor_check_all(config=config)
    # Both agents have populated recent surfaces; the ONLY issue is
    # the cross-agent overlap. Overall must be warn.
    assert report.overall == "warn"
    for agent in report.agents:
        assert agent.overall == "warn"


# ---------------------------------------------------------------------------
# Write-access probe (PLA-259)
# ---------------------------------------------------------------------------


def test_writable_surface_reports_writable_true(tmp_path: Path) -> None:
    """A normal populated surface probes writable=True and stays overall=ok —
    the write-access probe does not regress the healthy path (PLA-259).

    Sabotage-proof (executed): hardcoded ``writable=False`` in
    ``_probe_surface`` → overall flipped to error and this assertion failed;
    restored.
    """
    surface = tmp_path / "agent-alpha-mem"
    _seed_recent_md_files(surface, 2)

    health = doctor_check_agent("agent-alpha", config=_make_agent_config("agent-alpha", surface))

    assert health.surfaces[0].writable is True
    assert health.overall == "ok"


def test_unwritable_surface_is_flagged_error_with_actionable_issue(tmp_path: Path) -> None:
    """An existing surface kairix cannot write to (``:ro`` mount / wrong
    ownership, simulated with 0o500) rolls up to overall=error and carries an
    F21 issue naming WHICH path, WHICH permission, and HOW to fix (PLA-259).
    Skips on hosts where mode bits do not block writes (e.g. CI run as root).

    Sabotage-proof (executed): removed the not-writable branch from
    ``_rollup_overall`` → overall stayed warn and the ``== "error"``
    assertion failed; restored.
    """
    if os.geteuid() == 0:
        pytest.skip("permission denial cannot be simulated as root (touch succeeds despite 0o500)")
    surface = tmp_path / "agent-alpha-mem"
    _seed_recent_md_files(surface, 2)
    surface.chmod(0o500)  # r-x: readable + listable, but not writable
    try:
        health = doctor_check_agent("agent-alpha", config=_make_agent_config("agent-alpha", surface))
        sh = health.surfaces[0]
        if sh.writable:
            pytest.skip("filesystem ignores mode bits (write probe succeeded despite 0o500)")
        assert sh.writable is False
        assert health.overall == "error"
        joined = " ".join(sh.issues)
        assert "not writable" in joined
        assert str(surface) in joined  # which path
        assert "fix:" in joined and "next:" in joined  # how to fix
    finally:
        surface.chmod(0o700)
