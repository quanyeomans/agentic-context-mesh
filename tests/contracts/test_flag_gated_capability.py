"""Contract tests for FlagGatedCapability ABC (ADR-026 Track C).

Five contracts to prove:

1. Cannot instantiate the base class directly (ABC discipline).
2. Subclass missing :meth:`run_on` / :meth:`run_off` raises TypeError.
3. ``dispatch(read_flag=...)`` routes to :meth:`run_on` when the
   flag reader returns True; to :meth:`run_off` when False.
4. Both branches receive the right return type (Generic[T] honoured).
5. INFO marker lands in the log for the branch that ran.

Each test has a "Sabotage proof:" comment describing the mutation
that proves it has teeth.
"""

from __future__ import annotations

import logging

import pytest

from kairix.core.features.capability import FlagGatedCapability

pytestmark = pytest.mark.contract


class _RecordingCapability(FlagGatedCapability[str]):
    """Capability that records which branch ran (sets ``self.last_branch``)."""

    flag_name = "synthetic_test_flag"
    on_marker = "capability: ON branch ran (synthetic)"
    off_marker = "capability: OFF branch ran (synthetic)"

    def __init__(self) -> None:
        self.last_branch: str | None = None

    def run_on(self) -> str:
        self.last_branch = "on"
        return "ON_RESULT"

    def run_off(self) -> str:
        self.last_branch = "off"
        return "OFF_RESULT"


def test_dispatch_routes_to_run_on_when_flag_true() -> None:
    """When the flag reader returns True, ``dispatch`` calls
    :meth:`run_on` and returns its value.

    Sabotage proof: invert the ``if`` in :meth:`FlagGatedCapability.dispatch`
    to ``if not read_flag(...)``; the test fails because
    ``last_branch`` is ``"off"`` and the returned value is
    ``"OFF_RESULT"``.
    """
    cap = _RecordingCapability()
    result = cap.dispatch(read_flag=lambda name: True)
    assert result == "ON_RESULT"
    assert cap.last_branch == "on"


def test_dispatch_routes_to_run_off_when_flag_false() -> None:
    """When the flag reader returns False, ``dispatch`` calls
    :meth:`run_off` and returns its value.

    Sabotage proof: remove the trailing ``return self.run_off()`` line;
    the test fails because the returned value is ``None`` (Python's
    implicit return) and ``last_branch`` stays ``None``.
    """
    cap = _RecordingCapability()
    result = cap.dispatch(read_flag=lambda name: False)
    assert result == "OFF_RESULT"
    assert cap.last_branch == "off"


def test_dispatch_passes_correct_flag_name_to_reader() -> None:
    """``dispatch`` calls ``read_flag(self.flag_name)`` — never a
    hardcoded or substituted name.

    Sabotage proof: change the :meth:`dispatch` body to
    ``read_flag("wrong_name")``; the test fails because the
    ``observed`` set carries ``"wrong_name"`` instead of
    ``"synthetic_test_flag"``.
    """
    cap = _RecordingCapability()
    observed: list[str] = []

    def recording_reader(name: str) -> bool:
        observed.append(name)
        return True

    cap.dispatch(read_flag=recording_reader)
    assert observed == ["synthetic_test_flag"]


def test_dispatch_emits_on_marker_when_flag_true(caplog: pytest.LogCaptureFixture) -> None:
    """The ON marker lands in the INFO log when the ON branch runs —
    operators grep this line to confirm the rollout took effect.

    Sabotage proof: remove the ``logger.info(self.on_marker)`` call
    in :meth:`dispatch`'s True branch; the marker disappears from
    the captured log and the assertion fails.
    """
    cap = _RecordingCapability()
    with caplog.at_level(logging.INFO, logger="kairix.core.features.capability"):
        cap.dispatch(read_flag=lambda name: True)
    assert any("capability: ON branch ran (synthetic)" in m for m in caplog.messages), (
        f"expected ON marker in log; got {caplog.messages!r}"
    )


def test_dispatch_emits_off_marker_when_flag_false(caplog: pytest.LogCaptureFixture) -> None:
    """The OFF marker lands in the INFO log when the OFF branch runs.

    Sabotage proof: swap the on/off markers in the OFF branch's
    ``logger.info(...)`` call to ``self.on_marker``; the captured
    log contains the wrong marker and the assertion fails.
    """
    cap = _RecordingCapability()
    with caplog.at_level(logging.INFO, logger="kairix.core.features.capability"):
        cap.dispatch(read_flag=lambda name: False)
    assert any("capability: OFF branch ran (synthetic)" in m for m in caplog.messages), (
        f"expected OFF marker in log; got {caplog.messages!r}"
    )


def test_subclass_missing_run_on_cannot_instantiate() -> None:
    """A subclass that doesn't override :meth:`run_on` cannot be
    instantiated — the ABC requires the implementation.

    Sabotage proof: remove ``@abstractmethod`` from :meth:`run_on`
    in the base; ``Incomplete()`` instantiates without error and
    ``pytest.raises`` fails.
    """

    class Incomplete(FlagGatedCapability[str]):
        flag_name = "synthetic_incomplete"
        on_marker = "on"
        off_marker = "off"

        def run_off(self) -> str:
            return "off"

    with pytest.raises(TypeError, match="run_on"):
        Incomplete()  # type: ignore[abstract]  # intentional: proving abstract instantiation raises


def test_subclass_missing_run_off_cannot_instantiate() -> None:
    """Same contract for :meth:`run_off` — both branches required.

    Sabotage proof: remove ``@abstractmethod`` from :meth:`run_off`
    in the base; ``Incomplete()`` instantiates without error and
    ``pytest.raises`` fails.
    """

    class Incomplete(FlagGatedCapability[str]):
        flag_name = "synthetic_incomplete"
        on_marker = "on"
        off_marker = "off"

        def run_on(self) -> str:
            return "on"

    with pytest.raises(TypeError, match="run_off"):
        Incomplete()  # type: ignore[abstract]  # intentional: proving abstract instantiation raises


def test_generic_type_parameter_honoured() -> None:
    """The capability's return type is the ``T`` from the generic
    declaration — a subclass declaring ``FlagGatedCapability[int]``
    returns ``int`` from ``dispatch``.

    Sabotage proof: change ``run_on`` / ``run_off`` to return strings
    instead of ints; mypy --strict catches it (the test still passes
    at runtime, but the static contract is broken — F41 / mypy strict
    catches the regression).
    """

    class IntCapability(FlagGatedCapability[int]):
        flag_name = "synthetic_int_flag"
        on_marker = "on"
        off_marker = "off"

        def run_on(self) -> int:
            return 42

        def run_off(self) -> int:
            return 0

    cap = IntCapability()
    assert cap.dispatch(read_flag=lambda name: True) == 42
    assert cap.dispatch(read_flag=lambda name: False) == 0
