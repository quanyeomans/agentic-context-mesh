"""Contract C1 — Dockerfile must run as the `kairix` user (uid 995, gid 985).

Plan reference: ``2026-06-07-2-unified-container-supervisor.md`` (Plan 2 C1).

Two regression guards on the Dockerfile shape:

* :func:`test_dockerfile_declares_user_kairix` — the runtime stage must end
  with a ``USER kairix`` directive (KF-4 regression guard).
* :func:`test_dockerfile_creates_kairix_user_with_uid_995` — the kairix user
  must be created with ``--uid 995`` and ``--gid 985`` so bind-mounted files
  land on the host with the correct ownership (matches the host convention
  used across the deployed fleet).

Both flipped GREEN after Plan 2 Task 3 landed the Dockerfile refactor; the
xfail decorators were removed in Plan 2 Task 7 (close-out).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract


def _dockerfile_text() -> str:
    """Read the repo-root ``Dockerfile``.

    Resolved relative to this test file so the contract works in worktrees
    and CI checkouts without depending on the working directory.
    """
    df = Path(__file__).resolve().parents[2] / "Dockerfile"
    return df.read_text()


@pytest.mark.contract
def test_dockerfile_declares_user_kairix() -> None:
    """The Dockerfile MUST end with ``USER kairix`` (KF-4 regression guard).

    Plan 2 Task 3 landed the directive; this test pins it.
    """
    content = _dockerfile_text()
    assert re.search(r"^USER\s+kairix\s*$", content, re.MULTILINE), (
        "Dockerfile must declare `USER kairix` (KF-4 regression guard)"
    )


@pytest.mark.contract
def test_dockerfile_creates_kairix_user_with_uid_995() -> None:
    """The UID must be 995 and GID 985 to match host convention.

    Bind-mounted files written by the container land as ``kairix:kairix``
    on the host volume only when both ids match the host's reserved
    system range. Task 3 of Plan 2 lands the ``useradd``/``groupadd``
    invocations with these exact ids.
    """
    content = _dockerfile_text()
    assert "--uid 995" in content, "Dockerfile must create the kairix user with `--uid 995`"
    assert "--gid 985" in content, "Dockerfile must create the kairix group with `--gid 985`"
