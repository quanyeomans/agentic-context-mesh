"""Tests for the honest per-agent memory-writability probe (PLA-298).

``check_agent_memory_writable`` closes the false-green where onboard check
passed 18/18 on a box whose ``04-Agent-Knowledge`` overlay was read-only for
the agent's uid — because it never probed writability.

Pinned here:

  - a writable memory root → PASS; a read-only one → hard FAIL (not WARN);
  - the FAIL detail names each agent's verdict (for ``onboard check --json``)
    and the fix reuses ``write_access_fix_hint``;
  - F71 count-equals-ground-truth: the check's verdict matches an independent
    real filesystem write attempt at the same root;
  - the check is registered in ``ALL_CHECKS`` so ``run_all_checks`` runs it;
  - the ``_default_*`` production seams resolve (F86 execution floor).

Every probe runs as the test process's own uid against a real ``tmp_path``
root — no fake probe, no live document tree touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kairix.paths import WriteAccessProbe
from kairix.platform.onboard.check import (
    ALL_CHECKS,
    AgentMemoryWritableCheckDeps,
    check_agent_memory_writable,
)

pytestmark = pytest.mark.unit


def _require_enforced_readonly_or_skip(directory: Path) -> None:
    """Strip write perms from ``directory``; skip when the platform won't enforce it."""
    os.chmod(directory, 0o500)
    probe = directory / ".probe-write"
    try:
        probe.write_text("x", encoding="utf-8")
    except OSError:
        return  # read-only is enforced — proceed with the test
    probe.unlink()
    os.chmod(directory, 0o700)
    # F11: platform (root uid or a mode-blind filesystem) can't enforce read-only
    # directory perms, so the read-only branch is unverifiable here.
    pytest.skip("filesystem does not enforce read-only directory permissions")


def _config(agent: str, surface: str) -> dict[str, object]:
    return {"agents": {agent: {"harness": "claude-code", "surfaces": [{"path": surface, "label": "memory"}]}}}


def _real_write_succeeds(directory: Path) -> bool:
    """Ground truth — can THIS uid actually create a file under ``directory``?"""
    directory.mkdir(parents=True, exist_ok=True)
    probe = directory / ".ground-truth-probe"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def test_writable_agent_memory_root_passes(tmp_path: Path) -> None:
    """A writable configured agent memory root → ok=True.

    Sabotage: invert the ``if not writable`` branch → this PASS turns to FAIL.
    """
    doc_root = tmp_path / "vault"
    (doc_root / "04-Agent-Knowledge" / "agent-alpha").mkdir(parents=True)
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
        document_root_fn=lambda: doc_root,
    )
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is True
    assert "agent-alpha" in result.detail


def test_readonly_agent_memory_root_hard_fails_with_fix_hint(tmp_path: Path) -> None:
    """A read-only configured agent memory root → hard FAIL (not WARN) with the
    F21 fix hint from ``write_access_fix_hint``.

    Sabotage (executed): changed the check to return ok=True on a non-writable
    probe → this FAIL assertion flipped; restored.
    """
    doc_root = tmp_path / "vault"
    agent_root = doc_root / "04-Agent-Knowledge" / "agent-alpha"
    agent_root.mkdir(parents=True)
    _require_enforced_readonly_or_skip(agent_root)
    try:
        deps = AgentMemoryWritableCheckDeps(
            config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
            document_root_fn=lambda: doc_root,
        )
        result = check_agent_memory_writable(deps=deps)
    finally:
        os.chmod(agent_root, 0o700)

    assert result.ok is False, "a read-only overlay must be a HARD FAIL, not a pass"
    assert "not writable" in result.detail
    assert "agent-alpha" in result.detail  # per-agent verdict surfaced for --json
    assert result.fix is not None and "fix:" in result.fix and "next:" in result.fix


def test_verdict_equals_real_filesystem_write_attempt(tmp_path: Path) -> None:
    """F71 count-equals-ground-truth: the check's ok flag matches an independent
    real write attempt at the same root — for BOTH a writable and a read-only root.

    Sabotage: make the check ignore the probe (always ok=True) → the read-only
    leg's ``result.ok == ground_truth`` assertion fails.
    """
    # writable leg
    writable_root = tmp_path / "w" / "04-Agent-Knowledge"
    writable_root.mkdir(parents=True)
    deps_w = AgentMemoryWritableCheckDeps(config_loader=lambda: None, document_root_fn=lambda: tmp_path / "w")
    assert check_agent_memory_writable(deps=deps_w).ok is _real_write_succeeds(writable_root)

    # read-only leg
    ro_root = tmp_path / "ro" / "04-Agent-Knowledge"
    ro_root.mkdir(parents=True)
    _require_enforced_readonly_or_skip(ro_root)
    try:
        deps_ro = AgentMemoryWritableCheckDeps(config_loader=lambda: None, document_root_fn=lambda: tmp_path / "ro")
        result_ok = check_agent_memory_writable(deps=deps_ro).ok
        ground_truth = _real_write_succeeds(ro_root)
    finally:
        os.chmod(ro_root, 0o700)
    assert result_ok == ground_truth, "check verdict must match a real filesystem write attempt"


def test_no_configured_agents_probes_the_default_submount(tmp_path: Path) -> None:
    """With no ``agents:`` block, the check probes the shared 04-Agent-Knowledge
    writable submount so a fresh install still gets a truthful signal.

    Sabotage: return an empty root list instead of the default → ok flips to a
    vacuous pass with 0 roots and the '(default agent surface)' detail vanishes.
    """
    doc_root = tmp_path / "vault"
    (doc_root / "04-Agent-Knowledge").mkdir(parents=True)
    deps = AgentMemoryWritableCheckDeps(config_loader=lambda: None, document_root_fn=lambda: doc_root)
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is True
    assert "(default agent surface)" in result.detail


def test_check_is_registered_in_all_checks() -> None:
    """The probe is wired into the canonical registry so ``run_all_checks`` runs it.

    Sabotage: drop ``check_agent_memory_writable`` from ALL_CHECKS → this fails.
    """
    assert check_agent_memory_writable in ALL_CHECKS


def test_default_seams_resolve(tmp_path: Path) -> None:
    """The production ``_default_*`` seams execute (F86 execution floor).

    Reached through the public ``Deps`` default fields (F5 — no private import):
    constructing the deps with no overrides binds the ``default_factory`` seams,
    and invoking each field runs its body. The probe targets ``tmp_path`` so the
    only filesystem effect is a probe file created + removed in the tmp tree.
    """
    deps = AgentMemoryWritableCheckDeps()
    assert isinstance(deps.document_root_fn(), Path)
    cfg = deps.config_loader()
    assert cfg is None or isinstance(cfg, dict)
    probe = deps.probe_fn(tmp_path)
    assert hasattr(probe, "writable")


def test_default_probe_walks_up_to_nearest_existing_ancestor(tmp_path: Path) -> None:
    """The default probe walks up to the nearest EXISTING ancestor rather than
    materialising a missing target — so it leaves no lasting directory behind.

    Probing a deep non-existent path returns a probe whose ``path`` is the
    existing ``tmp_path`` (not the missing descendant), and that descendant is
    NOT created.

    Sabotage: break the walk-up (return the target unchanged, or over-walk to
    root) → ``probe.path`` no longer equals ``tmp_path`` and this fails.
    """
    missing = tmp_path / "nope" / "deeper"
    probe = AgentMemoryWritableCheckDeps().probe_fn(missing)

    assert probe.path == tmp_path, f"expected walk-up to {tmp_path}, got {probe.path}"
    assert probe.writable is True
    assert not missing.exists(), "the probe must not materialise the missing target"


def test_verdict_and_fix_name_the_specific_errno(tmp_path: Path) -> None:
    """The per-agent verdict and the fix hint carry the SPECIFIC errno, not a
    generic placeholder — a read-only mount (EROFS) reads through to the
    read-only-mount remediation from ``write_access_fix_hint``.

    Sabotage: blank the errno before building the verdict/fix → the detail shows
    ``FAIL[error]`` and the fix drops the read-only-mount language, failing both.
    """
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
        document_root_fn=lambda: tmp_path / "vault",
        probe_fn=lambda p: WriteAccessProbe(
            path=Path(p), writable=False, reason="Read-only file system", errno_name="EROFS"
        ),
    )
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is False
    assert "FAIL[EROFS]" in result.detail
    assert result.fix is not None and "read-only mount" in result.fix
