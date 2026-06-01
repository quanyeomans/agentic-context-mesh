"""F68 failure-injection contract test for :class:`BrowserLauncher`.

  * ``open`` → ``unavailable`` (headless / $DISPLAY missing / no browser)

The fake launcher returns ``True`` by default; tests pin the
``unavailable`` shape by constructing a launcher that records the URL
but returns ``False`` (matches the production ``webbrowser.open``
contract — returns False when no browser could be located).
"""

from __future__ import annotations

import pytest

from tests.fakes import FakeBrowserLauncher

pytestmark = pytest.mark.contract


def test_open_unavailable_when_no_browser_found() -> None:
    """``open`` returns ``False`` when no browser could be launched."""
    launcher = FakeBrowserLauncher(result=False)
    result = launcher.open("https://accounts.google.com/o/oauth2/v2/auth?client_id=x")
    assert result is False
    # The URL is still recorded so the operator + tests can see what
    # was attempted even when the launch failed.
    assert launcher.opened == ["https://accounts.google.com/o/oauth2/v2/auth?client_id=x"]
