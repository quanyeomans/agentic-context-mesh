"""Tests for the honest per-agent memory-writability probe (PLA-298 / #689).

``check_agent_memory_writable`` is the deploy health gate for agent memory. It
resolves each configured agent's write destination through the SAME
``resolve_writable_memory_dir`` the runtime write path uses — prefer the
``04-Agent-Knowledge`` overlay, fall back to the writable data dir on a
read-only / permission overlay — and bases the verdict on whether that RESOLVED
destination is writable.

Pinned here:

  - a writable overlay → PASS;
  - a read-only overlay WITH a writable data-dir fallback → PASS (surfaced as
    ``ok(fallback)``) — the hardened / read-only-root deploy case that must NOT
    fail the deploy gate (the v2026.7.2 rollback root cause);
  - genuinely NOWHERE writable (overlay AND data-dir fallback both unwritable, or
    a non-fallback errno such as ENOSPC) → hard FAIL with the F21 fix hint;
  - F71 count-equals-ground-truth: the check's ok flag matches an independent
    real write attempt at the resolved destination(s);
  - the check is registered in ``ALL_CHECKS``;
  - the ``_default_*`` production seams resolve (F86 execution floor).

Every real-filesystem probe runs as the test process's own uid against a
``tmp_path`` root — no live document tree or real data dir touched.
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
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".ground-truth-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def test_writable_agent_memory_root_passes(tmp_path: Path) -> None:
    """A writable configured agent memory overlay → ok=True.

    Sabotage: invert the ``if writable`` branch in ``_probe_agent_memory_roots``
    → this PASS turns to FAIL.
    """
    doc_root = tmp_path / "vault"
    (doc_root / "04-Agent-Knowledge" / "agent-alpha").mkdir(parents=True)
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
        document_root_fn=lambda: doc_root,
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
    )
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is True
    assert "agent-alpha" in result.detail
    assert "=ok" in result.detail and "fallback" not in result.detail


def test_readonly_overlay_with_writable_fallback_passes(tmp_path: Path) -> None:
    """A read-only overlay whose data-dir fallback IS writable → PASS.

    This is the hardened / read-only-root deploy case (the v2026.7.2 rollback
    root cause): the overlay is legitimately ``:ro`` and the write lands in the
    writable data-dir fallback, so the deploy gate must stay GREEN.

    Sabotage (executed): reverted the probe to judge the preferred overlay only
    → this PASS flipped to FAIL (reproducing the deploy-blocking behaviour);
    restored.
    """
    doc_root = tmp_path / "vault"
    agent_root = doc_root / "04-Agent-Knowledge" / "agent-alpha"
    agent_root.mkdir(parents=True)
    fallback_root = tmp_path / "data" / "agent-memory"
    fallback_root.mkdir(parents=True)
    _require_enforced_readonly_or_skip(agent_root)
    try:
        deps = AgentMemoryWritableCheckDeps(
            config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
            document_root_fn=lambda: doc_root,
            memory_fallback_root_fn=lambda: fallback_root,
        )
        result = check_agent_memory_writable(deps=deps)
    finally:
        os.chmod(agent_root, 0o700)

    assert result.ok is True, f"a read-only overlay with a working fallback must PASS: {result.detail}"
    assert "ok(fallback)" in result.detail
    assert "agent-alpha" in result.detail
    # The resolved destination is the per-agent subdir under the data-dir fallback,
    # mirroring the runtime write path's ``fallback_root / <agent>`` namespacing —
    # pins the ``_fallback_key`` sanitiser so the subdir is named for the agent.
    assert str(fallback_root / "agent-alpha") in result.detail


def test_no_writable_destination_hard_fails_with_fix_hint(tmp_path: Path) -> None:
    """Overlay AND data-dir fallback BOTH read-only → hard FAIL with the F21 fix hint.

    Sabotage: make the check ignore the resolved probe (always ok=True) → this
    FAIL assertion flips.
    """
    doc_root = tmp_path / "vault"
    agent_root = doc_root / "04-Agent-Knowledge" / "agent-alpha"
    agent_root.mkdir(parents=True)
    fallback_root = tmp_path / "data" / "agent-memory"
    fallback_root.mkdir(parents=True)
    _require_enforced_readonly_or_skip(agent_root)
    _require_enforced_readonly_or_skip(fallback_root)
    try:
        deps = AgentMemoryWritableCheckDeps(
            config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
            document_root_fn=lambda: doc_root,
            memory_fallback_root_fn=lambda: fallback_root,
        )
        result = check_agent_memory_writable(deps=deps)
    finally:
        os.chmod(agent_root, 0o700)
        os.chmod(fallback_root, 0o700)

    assert result.ok is False, "nowhere writable must be a HARD FAIL"
    assert "NO writable memory destination" in result.detail
    assert "agent-alpha" in result.detail  # per-agent verdict surfaced for --json
    assert result.fix is not None and "fix:" in result.fix and "next:" in result.fix


def test_non_fallback_errno_hard_fails(tmp_path: Path) -> None:
    """A non-fallback error (ENOSPC disk-full) on the overlay → hard FAIL.

    ``resolve_writable_memory_dir`` does NOT paper over a non-read-only/permission
    errno with a fallback (the real write would hit the same error), so the check
    must surface it as a hard FAIL rather than a false ``ok(fallback)``.

    Sabotage: broaden the fallback errno set to include ENOSPC → the resolver
    would fall back and this FAIL turns into a (wrong) fallback PASS.
    """
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
        document_root_fn=lambda: tmp_path / "vault",
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
        probe_fn=lambda p: WriteAccessProbe(
            path=Path(p), writable=False, reason="No space left on device", errno_name="ENOSPC"
        ),
    )
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is False
    assert "FAIL[ENOSPC]" in result.detail


def test_verdict_equals_real_persistability(tmp_path: Path) -> None:
    """F71 count-equals-ground-truth: the check's ok flag matches whether a real
    write can land at the resolved destination — the overlay OR the data-dir
    fallback — across writable, read-only-with-fallback, and nowhere-writable.

    Sabotage: make the check ignore the resolved probe → at least one leg's
    ``result.ok == ground_truth`` assertion fails.
    """

    def _verdict(doc_root: Path, fallback_root: Path) -> bool:
        deps = AgentMemoryWritableCheckDeps(
            config_loader=lambda: None,
            document_root_fn=lambda: doc_root,
            memory_fallback_root_fn=lambda: fallback_root,
        )
        return check_agent_memory_writable(deps=deps).ok

    # writable overlay → ground truth is the overlay itself
    w_doc = tmp_path / "w"
    w_overlay = w_doc / "04-Agent-Knowledge"
    w_overlay.mkdir(parents=True)
    w_fallback = tmp_path / "w-data"
    w_fallback.mkdir(parents=True)
    assert _verdict(w_doc, w_fallback) == (_real_write_succeeds(w_overlay) or _real_write_succeeds(w_fallback))

    # read-only overlay + writable fallback → ground truth is the fallback
    ro_doc = tmp_path / "ro"
    ro_overlay = ro_doc / "04-Agent-Knowledge"
    ro_overlay.mkdir(parents=True)
    ro_fallback = tmp_path / "ro-data"
    ro_fallback.mkdir(parents=True)
    _require_enforced_readonly_or_skip(ro_overlay)
    try:
        ground_truth = _real_write_succeeds(ro_overlay) or _real_write_succeeds(ro_fallback)
        verdict = _verdict(ro_doc, ro_fallback)
    finally:
        os.chmod(ro_overlay, 0o700)
    assert verdict == ground_truth, "read-only overlay + writable fallback must persist via the fallback"

    # nowhere writable → both read-only
    dead_doc = tmp_path / "dead"
    dead_overlay = dead_doc / "04-Agent-Knowledge"
    dead_overlay.mkdir(parents=True)
    dead_fallback = tmp_path / "dead-data"
    dead_fallback.mkdir(parents=True)
    _require_enforced_readonly_or_skip(dead_overlay)
    _require_enforced_readonly_or_skip(dead_fallback)
    try:
        ground_truth = _real_write_succeeds(dead_overlay) or _real_write_succeeds(dead_fallback)
        verdict = _verdict(dead_doc, dead_fallback)
    finally:
        os.chmod(dead_overlay, 0o700)
        os.chmod(dead_fallback, 0o700)
    assert verdict == ground_truth, "nowhere writable must be a hard fail"


def test_no_configured_agents_probes_the_default_submount(tmp_path: Path) -> None:
    """With no ``agents:`` block, the check probes the shared 04-Agent-Knowledge
    writable submount so a fresh install still gets a truthful signal.

    Sabotage: return an empty root list instead of the default → ok flips to a
    vacuous pass with 0 roots and the '(default agent surface)' detail vanishes.
    """
    doc_root = tmp_path / "vault"
    (doc_root / "04-Agent-Knowledge").mkdir(parents=True)
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: None,
        document_root_fn=lambda: doc_root,
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
    )
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
    assert isinstance(deps.memory_fallback_root_fn(), Path)
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
    """When NOTHING is writable, the per-agent verdict and fix carry the SPECIFIC
    errno — an all-read-only mount (EROFS) reads through to the read-only-mount
    remediation from ``write_access_fix_hint``.

    A probe that reports EROFS for every path means the overlay AND the data-dir
    fallback are both read-only → hard FAIL, and the errno propagates to both the
    verdict and the fix.

    Sabotage: blank the errno before building the verdict/fix → the detail shows
    ``FAIL[error]`` and the fix drops the read-only-mount language, failing both.
    """
    deps = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _config("agent-alpha", "04-Agent-Knowledge/agent-alpha"),
        document_root_fn=lambda: tmp_path / "vault",
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
        probe_fn=lambda p: WriteAccessProbe(
            path=Path(p), writable=False, reason="Read-only file system", errno_name="EROFS"
        ),
    )
    result = check_agent_memory_writable(deps=deps)

    assert result.ok is False
    assert "FAIL[EROFS]" in result.detail
    assert result.fix is not None and "read-only mount" in result.fix
