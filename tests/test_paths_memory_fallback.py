"""Unit tests for the writable-memory resolver — ``kairix.paths`` (PLA-296).

Pins the contract both the ``remember`` / ``memory_write`` and ``ingest_chat``
write paths rely on:

  - a writable preferred overlay is returned unchanged (no fallback, no scan
    root) — behaviour on a correctly-mounted deploy is untouched;
  - a read-only / permission-denied overlay (EROFS / EACCES / EPERM) falls back
    to the writable data dir, returns the fallback scan root, and warns loudly;
  - any OTHER probe failure (e.g. ENOSPC) is NOT masked by a fallback — the
    preferred path is returned so the caller's write surfaces the real error.

F1/F2-clean: the probe is injected through the ``probe_fn`` seam, never
monkeypatched, and no env vars are touched.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from kairix.paths import (
    WriteAccessProbe,
    agent_memory_fallback_root,
    data_dir,
    resolve_writable_memory_dir,
)

pytestmark = pytest.mark.unit


def _probe(writable: bool, errno_name: str = "") -> Callable[[str | Path], WriteAccessProbe]:
    """Return a probe_fn that reports a fixed verdict for any path."""

    def _fn(path: str | Path) -> WriteAccessProbe:
        return WriteAccessProbe(
            path=Path(path),
            writable=writable,
            reason="" if writable else "denied",
            errno_name=errno_name,
        )

    return _fn


def test_writable_overlay_is_returned_unchanged(tmp_path: Path) -> None:
    """Writable preferred overlay → returned verbatim, no fallback, no scan root.

    Sabotage: make ``resolve_writable_memory_dir`` always return the fallback →
    ``used_fallback``/``write_dir`` assertions fail.
    """
    preferred = tmp_path / "vault" / "04-Agent-Knowledge" / "agent-alpha"
    fallback = tmp_path / "data" / "agent-memory" / "agent-alpha"
    resolved = resolve_writable_memory_dir(
        preferred,
        fallback,
        label="agent 'agent-alpha'",
        fallback_scan_root=tmp_path / "data" / "agent-memory",
        probe_fn=_probe(writable=True),
    )
    assert resolved.write_dir == preferred
    assert resolved.used_fallback is False
    assert resolved.scan_root is None


@pytest.mark.parametrize("errno_name", ["EROFS", "EACCES", "EPERM"])
def test_readonly_overlay_falls_back_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, errno_name: str
) -> None:
    """Read-only / permission-denied overlay → data-dir fallback + scan root + WARN.

    Sabotage: drop the fallback branch (always return preferred) → the
    ``write_dir == fallback`` and ``scan_root`` assertions fail.
    """
    preferred = tmp_path / "vault" / "04-Agent-Knowledge" / "agent-alpha"
    fallback = tmp_path / "data" / "agent-memory" / "agent-alpha"
    scan_root = tmp_path / "data" / "agent-memory"
    with caplog.at_level(logging.WARNING, logger="kairix.paths"):
        resolved = resolve_writable_memory_dir(
            preferred,
            fallback,
            label="agent 'agent-alpha'",
            fallback_scan_root=scan_root,
            probe_fn=_probe(writable=False, errno_name=errno_name),
        )
    assert resolved.write_dir == fallback
    assert resolved.used_fallback is True
    assert resolved.scan_root == scan_root
    warned = [r for r in caplog.records if "not writable" in r.getMessage()]
    assert warned, "a non-writable overlay must be surfaced with a loud WARN"


def test_non_permission_error_is_not_masked_by_fallback(tmp_path: Path) -> None:
    """A non-fallback errno (e.g. ENOSPC) keeps the preferred path so the real
    error surfaces at write time rather than being papered over.

    Sabotage: fall back on ANY non-writable probe → ``used_fallback`` becomes
    True and this assertion fails.
    """
    preferred = tmp_path / "vault" / "04-Agent-Knowledge" / "agent-alpha"
    fallback = tmp_path / "data" / "agent-memory" / "agent-alpha"
    resolved = resolve_writable_memory_dir(
        preferred,
        fallback,
        label="agent 'agent-alpha'",
        fallback_scan_root=tmp_path / "data" / "agent-memory",
        probe_fn=_probe(writable=False, errno_name="ENOSPC"),
    )
    assert resolved.write_dir == preferred
    assert resolved.used_fallback is False
    assert resolved.scan_root is None


def test_fallback_root_is_under_the_writable_data_dir() -> None:
    """The fallback base resolves beneath the persistent data dir (F94 posture).

    Sabotage: point ``agent_memory_fallback_root`` at the document root → the
    ``data_dir()``-prefix assertion fails.
    """
    root = agent_memory_fallback_root()
    assert root == data_dir() / "agent-memory"
    assert root.name == "agent-memory"
