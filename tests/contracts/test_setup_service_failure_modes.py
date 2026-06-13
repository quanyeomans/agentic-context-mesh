"""F43 + F68 contract for the SetupService Protocol's ``config_file_path``.

This is the live reference for F43's behavioural-parity strengthening
(EPIC #499 phase 1): the positive contract is proved by ONE parametrized
body run over BOTH the real ``KairixSetupService`` (through its
``SetupServiceDeps`` seam) and the canonical ``FakeSetupService`` from
``tests/fakes.py``. Co-asserting the same observable through both impls
is what would have caught session-escape 7 — where the fake inverted
production's done-semantics while every separate-body suite stayed green.

The ``raises`` failure shape (F68) is genuinely single-impl: only the
real backend resolves a config target through an injectable resolver
that can fail; the fake returns a constructor knob and has no analogue.
It carries the ``# F43-single-impl:`` rationale the rule requires.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kairix.platform.setup.backends import KairixSetupService, SetupServiceDeps
from kairix.platform.setup.service import SetupService
from tests.fakes import FakeSetupService

pytestmark = pytest.mark.contract

# The shared resolvable target both impls are configured against. The
# parity body asserts both surface THIS exact path — the fake's knob and
# the real backend's resolver must agree on the observable, never just
# "some non-empty string each". ``config_file_path()`` returns a str on
# both impls; the real backend's resolver yields a Path (its declared
# type) that the backend stringifies, so the observable is this string.
_RESOLVED_TARGET = "/var/lib/kairix/kairix.config.local.yaml"


def _real_service() -> SetupService:
    """The production backend, its config-target resolver pinned to the
    shared path through the canonical ``SetupServiceDeps`` seam."""
    return KairixSetupService(deps=SetupServiceDeps(config_target_fn=lambda: Path(_RESOLVED_TARGET)))


def _fake_service() -> SetupService:
    """The canonical fake, its config-file knob set to the shared path."""
    return FakeSetupService(config_file=_RESOLVED_TARGET)


# Real + fake behind one parameter — the F43 parity shape. ``name``
# labels the case; ``factory`` builds the impl under test.
_IMPLEMENTATIONS: list[tuple[str, Callable[[], SetupService]]] = [
    ("real", _real_service),
    ("fake", _fake_service),
]


@pytest.mark.parametrize("name,factory", _IMPLEMENTATIONS)
def test_config_file_path_surfaces_the_resolved_target(name: str, factory: Callable[[], SetupService]) -> None:
    """Both impls return the resolved config path verbatim — non-empty
    and equal to the target they were configured against.

    The save/done screens render this line so the operator knows which
    file to carry to a new machine; a fake that returned a *different*
    truthy string (or empty) would render misleading copy while every
    separate-body test still passed. Running the SAME assertion through
    real and fake pins them to one observable.

    Sabotage proof (executed): change the fake's ``config_file_path`` to
    ``return self._config_file + "-DRIFT"`` → this body fails for the
    ``fake`` param (``... != /var/lib/...``) while ``real`` still passes,
    catching exactly the real/fake divergence F43 targets. Restored.
    """
    service = factory()
    result = service.config_file_path()
    assert result == _RESOLVED_TARGET, f"{name} impl must surface the resolved target verbatim"
    assert result != "", f"{name} impl must never blank the config path when one resolves"


# F43-single-impl: the fake's config_file_path() returns a constructor
# knob and cannot raise — only the real backend resolves through an
# injectable target resolver that can fail. No fake-side analogue exists,
# so this ``raises`` failure-mode probe is genuinely real-only.
def test_config_file_path_raises_when_target_resolution_fails() -> None:
    """``raises`` shape (F68): a failing target resolver propagates,
    never masks. The service must not swallow a resolver error into a
    fabricated path — the route tier owns rendering decisions (review
    M2's lesson: wrong rescue copy is worse than a visible error).
    """
    service = KairixSetupService(deps=SetupServiceDeps(config_target_fn=_raise_oserror))
    with pytest.raises(OSError, match="config target unavailable"):
        service.config_file_path()


def _raise_oserror() -> Path:
    raise OSError("config target unavailable")
