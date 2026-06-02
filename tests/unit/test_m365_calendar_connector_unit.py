"""Unit tests for the m365_calendar connector's config validation.

Targets the #382 fix: ``M365CalendarConfig`` rejects windows that exceed
the Graph ``calendarView/delta`` 13-month cap at construction time so
the failure surfaces in ``kairix config validate`` rather than at the
first sync tick (where the operator just sees a generic HTTP 400).

Sabotage proof: deleting the ``__post_init__`` validator flips
:func:`test_window_total_above_cap_raises` because construction stops
raising. Mutating ``MAX_WINDOW_TOTAL_DAYS`` upward past 455 flips
:func:`test_default_window_is_under_cap` because the default total
moves outside the assertion window.
"""

from __future__ import annotations

import pytest

from kairix.connectors.m365_calendar.connector import (
    DEFAULT_WINDOW_DAYS_BACK,
    DEFAULT_WINDOW_DAYS_FORWARD,
    MAX_WINDOW_TOTAL_DAYS,
    M365CalendarConfig,
)

pytestmark = pytest.mark.unit


def _config(*, back: int, forward: int) -> M365CalendarConfig:
    return M365CalendarConfig(
        user_id="user@example.com",
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",  # pragma: allowlist secret
        window_days_back=back,
        window_days_forward=forward,
    )


def test_default_window_is_under_cap() -> None:
    total = DEFAULT_WINDOW_DAYS_BACK + DEFAULT_WINDOW_DAYS_FORWARD
    assert total <= MAX_WINDOW_TOTAL_DAYS, (
        f"defaults {DEFAULT_WINDOW_DAYS_BACK}+{DEFAULT_WINDOW_DAYS_FORWARD}={total} "
        f"must fit under MAX_WINDOW_TOTAL_DAYS={MAX_WINDOW_TOTAL_DAYS}"
    )


def test_window_total_at_cap_accepted() -> None:
    cfg = _config(back=90, forward=MAX_WINDOW_TOTAL_DAYS - 90)
    assert cfg.window_days_back + cfg.window_days_forward == MAX_WINDOW_TOTAL_DAYS


def test_window_total_above_cap_raises() -> None:
    with pytest.raises(ValueError, match="exceeds Graph calendarView/delta"):
        _config(back=90, forward=MAX_WINDOW_TOTAL_DAYS - 90 + 1)


def test_window_total_legacy_default_above_cap_raises() -> None:
    # 90 + 365 = 455 — the exact shape that produced the production
    # HTTP 400 in #382 before the validator landed.
    with pytest.raises(ValueError, match="13-month cap"):
        _config(back=90, forward=365)
