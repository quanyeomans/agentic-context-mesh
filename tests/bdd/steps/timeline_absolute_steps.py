"""Step definitions for timeline_absolute.feature.

Re-uses shared steps from mcp_timeline_steps.py for common assertions.
Only defines steps unique to the absolute date scenarios.
"""

from pytest_bdd import parsers, then

# Import _state from the existing timeline steps so we share state
from tests.bdd.steps.mcp_timeline_steps import _state


@then(parsers.re(r'the timeline response time_window start contains "(?P<expected>[^"]*)"'))
def time_window_start_contains(expected):
    tw = _state["result"]["time_window"]
    assert expected in tw["start"], f"Expected start to contain {expected!r}, got {tw['start']!r}"
