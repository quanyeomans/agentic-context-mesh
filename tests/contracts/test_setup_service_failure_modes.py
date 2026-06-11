"""F68 failure-injection contract tests for the SetupService Protocol.

Seed file (EPIC #499 wave 5 / review H5 grows this to every method):
``config_file_path`` is the first net-new Protocol method landed after
F68's repo-wide Protocol harvest (#499 phase 0), so it gets its
failure-mode coverage in the same tranche instead of a baseline entry.

Both implementations are exercised per the repo's contract-test
convention: the real ``KairixSetupService`` through its
``SetupServiceDeps`` seam and the canonical ``FakeSetupService`` from
``tests/fakes.py`` — the pair must degrade the same way so route tests
written against the fake stay honest about production behaviour.
"""

from __future__ import annotations

import pytest

from kairix.platform.setup.backends import KairixSetupService, SetupServiceDeps
from tests.fakes import FakeSetupService

pytestmark = pytest.mark.contract


def test_config_file_path_raises_when_target_resolution_fails() -> None:
    """``raises`` shape: a failing target resolver propagates, never masks.

    The save/done screens treat the path as advisory display copy; the
    service must not swallow a resolver error into a fabricated path —
    the route tier owns rendering decisions (review M2's lesson: wrong
    rescue copy is worse than a visible error).
    """
    service = KairixSetupService(
        deps=SetupServiceDeps(
            config_target_fn=_raise_oserror,
        )
    )
    with pytest.raises(OSError, match="config target unavailable"):
        service.config_file_path()


def test_config_file_path_returns_empty_only_from_the_fake_blank_knob() -> None:
    """``returns_empty`` shape: the fake's blank knob models "no file yet".

    The done screen renders its config-file line only when the path is
    truthy; the fake's empty-string knob is the canonical way route
    tests exercise that branch. The real backend never returns empty
    (resolution always lands on overlay/env/cwd/XDG) — pinned here so
    fake and real cannot silently diverge on emptiness semantics.
    """
    fake = FakeSetupService(config_file="")
    assert fake.config_file_path() == ""

    real = KairixSetupService(
        deps=SetupServiceDeps(config_target_fn=lambda: "/var/lib/kairix/kairix.config.local.yaml")
    )
    assert real.config_file_path() != ""


def _raise_oserror() -> str:
    raise OSError("config target unavailable")
