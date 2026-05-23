"""Observability hooks for feature-flag activations.

See ``docs/architecture/feature-flag-architecture.md`` §3.3 step 3 —
``flag(name)`` emits a counter via the existing search-logger telemetry
hook so the ``probe-config`` health envelope reports which flags are
active.

PR-2 ships the minimal surface:

* :func:`log_first_activation` — INFO log once per (process, flag).
* :func:`emit_activation_counter` — no-op default; future telemetry
  wiring binds the actual counter sink without changing call sites.

Future PRs swap the no-op default for the search-logger hook once that
sink lives in the warm pipeline. Keeping the seam in place from PR-2
means the resolver is wired correctly the day a real counter lands.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


# In-process activation log — prevents the INFO line firing more than
# once per (process, flag) pair. The resolver also caches at its layer,
# so the second call short-circuits before reaching here; this set is
# the belt-and-braces safeguard.
_logged_activations: set[str] = set()


def log_first_activation(name: str, *, effective: bool, source: str) -> None:
    """Emit one INFO log the first time a flag resolves in this process.

    Subsequent calls for the same ``name`` are silent. ``source`` is
    one of ``"env"``, ``"config"``, or ``"default"`` — recorded so an
    operator reading the log can see which layer of §3.4 won.
    """
    if name in _logged_activations:
        return
    _logged_activations.add(name)
    logger.info(
        "feature_flag.activation name=%r effective=%r source=%r",
        name,
        effective,
        source,
    )


# Counter-sink type: a callable taking (flag_name, effective) so future
# telemetry wiring can swap in a real sink without changing call sites.
CounterSink = Callable[[str, bool], None]


def noop_counter(_name: str, _effective: bool) -> None:
    """Default counter sink — no-op until PR-3 wires the search-logger hook."""
    # Intentionally empty — future telemetry wiring binds the real sink.


_counter_sink: CounterSink = noop_counter


def set_counter_sink(sink: CounterSink) -> None:
    """Install a counter sink for flag activations.

    Future telemetry binding sets this once at warm-up, then resolver
    calls forward (name, effective) into it. Process-global; tests that
    need to observe activations can install a list-appender sink and
    inspect the captured calls.
    """
    global _counter_sink
    _counter_sink = sink


def emit_activation_counter(name: str, effective: bool) -> None:
    """Forward an activation event to the configured counter sink.

    No-op by default; future PRs bind the search-logger hook via
    :func:`set_counter_sink` so the ``probe-config`` envelope can
    report active flags without touching the resolver.
    """
    _counter_sink(name, effective)


def reset_observability_state() -> None:
    """Test-only helper: clear the per-process activation cache.

    Tests that need to re-observe the first-activation log restore the
    pristine state by calling this. Production code never calls it.
    """
    _logged_activations.clear()
