"""FlagGatedCapability ABC — ADR-026 Track C.

Today every feature-flag callsite repeats the same scaffold:

* A ``dispatch_<name>_sync(read_flag, on_branch, off_branch)`` function
* Two helper functions (``run_via_<name>_connector`` for ON,
  ``<name>_off_branch_noop`` for OFF)
* INFO logs in each branch with distinct markers operators grep for
* BDD feature + integration test exercising both branches (F54)

Per-flag scaffold ≈265 lines, of which ≈75% is the same shape
repeated for the next flag. ADR-026 §6 collapses that scaffold into a
single subclass:

.. code-block:: python

    class MaintenanceLoopCapability(FlagGatedCapability[None]):
        flag_name = "maintenance_loop"
        on_marker = "worker: maintenance loop running (flag ON)"
        off_marker = "worker: maintenance loop gated off (flag OFF)"

        def run_on(self) -> None:
            return run_maintenance_loop_tick()

        def run_off(self) -> None:
            return None

The base's :meth:`dispatch` method does the flag read + branch + log
in one place. Tests pin the flag via ``capability.dispatch(read_flag=
FakeFeatureFlagResolver().with_flag(...).get)`` — no monkey-patching,
no global mutation.

Generic over the return type so multiple call-site patterns fit:

* Pattern 1 (connector-gating ``connector_*`` flags):
  ``T = ConnectorSyncResult``
* Pattern 2 (``maintenance_loop``): ``T = None`` (Deps-injected, no
  return value)
* Pattern 3 (``pipeline_status_emit``): ``T = sqlite3.Connection | None``
  (selects whether to write the timeline)

This file ships the ABC + tests. Migration of the 11 callsites is
C.2-C.4 — separate commits so each migration can be reviewed
independently and rolled back if it surfaces an unforeseen edge case.
F78 (the structural check "every flag in REGISTRY has a corresponding
:class:`FlagGatedCapability` subclass") replaces the regex-based F54
once migration is complete.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import ClassVar, Generic, TypeVar

logger = logging.getLogger(__name__)


T = TypeVar("T")


def _default_flag_reader(name: str) -> bool:
    """Production default for :meth:`FlagGatedCapability.dispatch`'s
    ``read_flag`` argument.

    Delegates to :func:`kairix.core.features.flag` — the resolver
    that reads ``kairix.config.yaml`` + ``KAIRIX_FEATURE_*`` env
    overrides. The import is deferred so importing
    :mod:`kairix.core.features.capability` doesn't load the resolver
    module at import time (the registry-resolver module pulls in the
    full feature surface).
    """
    from kairix.core.features.resolver import flag as _prod_flag

    return _prod_flag(name)


class FlagGatedCapability(Generic[T], ABC):
    """A capability whose code path is gated by a feature flag.

    Subclasses declare:

    * :attr:`flag_name` — the flag this capability reads (must exist
      in :data:`kairix.core.features.registry.REGISTRY`, enforced by
      F52 + the upcoming F78).
    * :attr:`on_marker` — INFO log line emitted when the ON branch
      runs. Operators grep this to confirm the rollout took effect.
    * :attr:`off_marker` — INFO log line emitted when the OFF branch
      runs. Pairs with ``on_marker`` to make the operator-visible
      cutover signal unambiguous.
    * :meth:`run_on` — the ON-branch implementation. The work the
      capability does when the flag is True. Required, no default.
    * :meth:`run_off` — the OFF-branch implementation. The default /
      legacy / no-op path when the flag is False. Required, no
      default — a flag with no OFF branch is structurally a "rip out
      the flag" candidate, not a capability.

    The base's :meth:`dispatch` reads the flag, emits the appropriate
    marker, and runs the corresponding branch. That's the only
    behaviour — every flag-gating callsite shrinks to a subclass
    declaration + one ``capability.dispatch()`` call at the use site.
    """

    flag_name: ClassVar[str]
    on_marker: ClassVar[str]
    off_marker: ClassVar[str]

    @abstractmethod
    def run_on(self) -> T:
        """The ON-branch implementation. Runs when ``flag(flag_name)`` is True."""

    @abstractmethod
    def run_off(self) -> T:
        """The OFF-branch implementation. Runs when ``flag(flag_name)`` is False."""

    def dispatch(self, read_flag: Callable[[str], bool] = _default_flag_reader) -> T:
        """Read the flag, emit the marker, run the corresponding branch.

        ``read_flag`` is injectable so tests can pin the flag value
        through a :class:`~kairix.core.features.resolver.FakeFeatureFlagResolver`
        without monkey-patching the resolver module or setting
        ``KAIRIX_FEATURE_*`` env vars (F1 / F2 clean by construction).
        Production callers leave ``read_flag`` defaulted; it reads
        ``kairix.config.yaml`` + ``KAIRIX_FEATURE_*`` overrides via
        the canonical resolver.
        """
        if read_flag(self.flag_name):
            logger.info(self.on_marker)
            return self.run_on()
        logger.info(self.off_marker)
        return self.run_off()


__all__ = [
    "FlagGatedCapability",
]
