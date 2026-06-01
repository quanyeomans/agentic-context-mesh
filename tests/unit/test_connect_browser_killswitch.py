"""Unit coverage for the ``KAIRIX_CONNECT_DISABLE_BROWSER`` kill-switch.

2026-06-01 incident: agent test runs against ``kairix.connect.oauth2.*``
leaked real ``webbrowser.open`` calls with placeholder ``client_id`` values,
producing a stream of "client_id not valid" approval popups on the
operator's machine. The injection seams (``browser=`` constructor kwarg)
are correct, but a single test that forgets to inject (or a subprocess
that escapes the ``_inject`` patching pattern) silently fires.

The kill-switch in ``kairix.paths.connect_browser_disabled`` is the
defence-in-depth: when the env var is set, every default-browser
fallback short-circuits to a no-op and emits an F21-shaped warning.

``tests/conftest.py`` sets the env var to ``"1"`` at import time so the
default for the test session is ON. These tests pin the contract by
driving the public ``GoogleOAuth2Flow`` + ``SlackOAuth2Flow.authorize``
surface with NO ``browser=`` injection — so the production default
browser runs, hits the kill-switch, and returns without firing
``webbrowser.open``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI, GoogleOAuth2Flow
from kairix.connect.oauth2.slack import SLACK_TOKEN_URI, SlackOAuth2Flow
from kairix.connect.protocols import CapturedTokens, ClientCredentials
from kairix.paths import connect_browser_disabled
from tests.fakes import FakeCallbackListener

pytestmark = pytest.mark.unit


def test_conftest_sets_kill_switch_on_for_session() -> None:
    """tests/conftest.py loaded — env var defaulted to ``"1"`` for the test session."""
    assert os.environ.get("KAIRIX_CONNECT_DISABLE_BROWSER") == "1", (
        f"expected KAIRIX_CONNECT_DISABLE_BROWSER='1' (set by tests/conftest.py), "
        f"got {os.environ.get('KAIRIX_CONNECT_DISABLE_BROWSER')!r}"
    )


def _capture_no_op(_c: ClientCredentials, _code: str, _ru: str) -> CapturedTokens:
    """Test exchanger — returns a minimal CapturedTokens so authorize() completes."""
    return CapturedTokens(
        refresh_token="rt-from-test",
        access_token="at-from-test",
        token_uri=GOOGLE_TOKEN_URI,
    )


def _slack_no_op(_c: ClientCredentials, _code: str, _ru: str) -> CapturedTokens:
    """Slack-shape test exchanger — bot_token populated, refresh empty."""
    return CapturedTokens(
        refresh_token="",
        access_token="",
        token_uri=SLACK_TOKEN_URI,
        bot_token="xoxb-from-test",
    )


def test_google_default_browser_short_circuits_when_disabled(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Driving the public ``GoogleOAuth2Flow.authorize`` with no ``browser=`` injection.

    With ``KAIRIX_CONNECT_DISABLE_BROWSER=1`` (set by conftest), the default
    browser path inside the flow MUST short-circuit — authorize() still
    completes (returns the test-exchanger's tokens) but no real browser opens.
    """
    cs = tmp_path / "cs.json"
    cs.write_text('{"installed":{"client_id":"x","client_secret":"y"}}')
    caplog.set_level(logging.WARNING, logger="kairix.connect.oauth2.google")

    # NO browser= kwarg — the default browser path runs and MUST hit the
    # kill-switch (otherwise this test would fire a real webbrowser.open).
    flow = GoogleOAuth2Flow(
        service_area="gmail",
        client_secret_path=cs,
        token_exchanger=_capture_no_op,
    )
    tokens = flow.authorize(listener=FakeCallbackListener())
    assert tokens.refresh_token == "rt-from-test"

    record_msgs = [r.getMessage() for r in caplog.records]
    assert any("suppressed by KAIRIX_CONNECT_DISABLE_BROWSER" in m for m in record_msgs), (
        f"expected suppression warning from google default browser, got: {record_msgs!r}"
    )
    assert any("fix:" in m and "next:" in m and "run:" in m for m in record_msgs), (
        f"expected F21 markers in warning, got: {record_msgs!r}"
    )


def test_slack_default_browser_short_circuits_when_disabled(caplog: pytest.LogCaptureFixture) -> None:
    """Driving the public ``SlackOAuth2Flow.authorize`` with no ``browser=`` injection."""
    caplog.set_level(logging.WARNING, logger="kairix.connect.oauth2.slack")
    flow = SlackOAuth2Flow(
        workspace="alpha",
        client_id="cid",
        client_secret="csec",  # pragma: allowlist secret
        token_exchanger=_slack_no_op,
    )
    tokens = flow.authorize(listener=FakeCallbackListener())
    assert tokens.bot_token == "xoxb-from-test"

    record_msgs = [r.getMessage() for r in caplog.records]
    assert any("suppressed by KAIRIX_CONNECT_DISABLE_BROWSER" in m for m in record_msgs), (
        f"expected suppression warning from slack default browser, got: {record_msgs!r}"
    )
    assert any("fix:" in m and "next:" in m and "run:" in m for m in record_msgs), (
        f"expected F21 markers in warning, got: {record_msgs!r}"
    )


def test_paths_helper_returns_false_when_env_unset() -> None:
    """``connect_browser_disabled(env={})`` returns False — pins the production-unset path.

    Production callers (the default-browser fallback inside each Flow's
    authorize()) leave ``env=None`` so the live ``os.environ`` is read;
    an operator running ``kairix connect <svc>`` on the host with the
    var unset should get the real browser. Passing an empty dict drives
    the same unset branch without touching real env vars (F2-clean).
    """
    assert connect_browser_disabled(env={}) is False


@pytest.mark.parametrize("truthy", ["1", "true", "TRUE", "yes", "Yes"])
def test_paths_helper_truthy_values(truthy: str) -> None:
    """``connect_browser_disabled(env=...)`` accepts the documented truthy spellings."""
    assert connect_browser_disabled(env={"KAIRIX_CONNECT_DISABLE_BROWSER": truthy}) is True


@pytest.mark.parametrize("falsy", ["0", "false", "no", "", "anything-else"])
def test_paths_helper_falsy_values(falsy: str) -> None:
    """``connect_browser_disabled(env=...)`` rejects non-truthy values (incl. empty + nonsense)."""
    assert connect_browser_disabled(env={"KAIRIX_CONNECT_DISABLE_BROWSER": falsy}) is False
