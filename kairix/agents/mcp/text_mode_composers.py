"""Registry mapping CLI subcommand → (envelope_to_result, format_text) pair.

Used by :mod:`kairix.agents.mcp.client_dispatcher` to render text-mode
output from a warm-MCP envelope. Subcommands without a registered
composer fall through to in-process dispatch — the registry is the
gate (PR 2.8 / #421).

Each entry's :attr:`TextModeComposer.from_envelope` callable accepts a
dict (the MCP tool envelope) and returns an in-process result object.
:attr:`TextModeComposer.format_text` is the existing subcommand-specific
text formatter that takes ``(result, argv)`` — the argv slice lets the
formatter extract per-call flags (e.g. ``--limit`` for timeline,
``--top-k`` for contradict).

When the dispatcher routes a subcommand, it calls::

    envelope = mcp_client.call_tool(name, kwargs).payload
    composer = get_composer(subcommand)
    result = composer.from_envelope(envelope)
    print(composer.format_text(result, argv))

Import direction is one-way: CLI / use-case modules import this module
and call :func:`register_composer` at import time. This module MUST NOT
import any CLI or use-case modules — that would create a circular
dependency. Registration lives on the CLI side; the registry is leaf.

See :mod:`kairix.agents.mcp._composer_init` for the canonical wiring
that registers every PR 2.1-2.7 composer at the dispatcher's first
import.

F42-clean: the public boundary uses a frozen dataclass, never bare dict.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextModeComposer:
    """One subcommand's envelope→text rendering pair.

    Attributes:
        from_envelope: Callable that accepts the MCP tool envelope dict
            and returns the use-case's result object.
        format_text: Callable that accepts ``(result, argv)`` and
            returns the operator-facing text. ``argv`` is the original
            CLI argv slice (post subcommand strip) so the formatter can
            extract per-call flags that aren't in the envelope (e.g.
            ``--limit`` for timeline, ``--top-k`` / ``--threshold`` for
            contradict).
        name: Subcommand name for diagnostics (matches the dispatcher's
            ``MCP_TOOL_MAP`` key).
    """

    from_envelope: Callable[[dict[str, Any]], Any]
    format_text: Callable[..., str]
    name: str


# Populated by import-time registration; one entry per composer-equipped
# subcommand. Test files mutate this dict directly only to reset state
# between scenarios — production callers go through register_composer().
_REGISTRY: dict[str, TextModeComposer] = {}


def register_composer(subcommand: str, composer: TextModeComposer) -> None:
    """Register a composer.

    Called from each subcommand's CLI / use-case module at import time.
    Re-registering the same subcommand replaces the prior entry
    (last-write-wins) so import-order regressions never silently keep
    stale composers around.
    """
    _REGISTRY[subcommand] = composer


def get_composer(subcommand: str) -> TextModeComposer | None:
    """Return the registered composer or ``None`` if not registered.

    The dispatcher uses this as the text-mode routing gate: if
    :func:`get_composer` returns ``None`` for the current subcommand,
    the dispatcher falls through to the in-process CLI path even when
    the warm MCP server is responsive.
    """
    return _REGISTRY.get(subcommand)


def list_registered() -> tuple[str, ...]:
    """Diagnostics — return every registered subcommand name, sorted.

    Used by ``kairix features status`` (and ``tool_features_status``)
    to surface which CLI subcommands the warm-MCP dispatcher can render
    in text mode. Sorted output keeps the listing stable across
    invocations.
    """
    return tuple(sorted(_REGISTRY))


def unregister_composer(subcommand: str) -> TextModeComposer | None:
    """Remove the registered composer (if any) and return it.

    Returns ``None`` when no composer was registered for ``subcommand``.
    The public surface lets test scaffolding clear an entry without
    reaching into the private ``_REGISTRY`` dict (F24 / F5-clean) and
    lets the production code path observe the same gate-falls-through
    behaviour the test asserts on.
    """
    return _REGISTRY.pop(subcommand, None)
