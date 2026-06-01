"""E2E composed-path test for kairix connect slack (F48 + ADR-032 §"E2E").

Carries ``@pytest.mark.e2e`` and runs in CI Stage 4.5. Exercises the
full operator journey end-to-end for Slack:

  1. Operator runs ``python -m kairix.cli connect slack --workspace
     test --client-id <id> --client-secret <secret> --store=file``.
  2. The localhost listener captures a callback (test simulates this
     via a fake injected before argparse runs).
  3. The captured bot_token lands in the configured
     ``$KAIRIX_SECRETS_FILE`` under the per-workspace canonical name
     ``KAIRIX_CONNECTOR_SLACK_TEST_BOT_TOKEN``.
  4. The Slack connector picks up the stored token and constructs a
     :class:`kairix.connect.refresh.StaticRefreshableToken` (per
     ADR-032 §"Refresh handling" — Slack bot tokens never refresh).
  5. ``StaticRefreshableToken.headers()`` produces a valid
     ``Authorization: Bearer xoxb-...`` header — the connector's
     HTTP client read on every API call.

The composed factory here is the connect CLI's ``ConnectDeps`` + the
refresh module's ``StaticRefreshableToken``; the "ingest" is the token
write; the "query" is the subsequent refresh-token read producing
valid auth headers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.connect.refresh import StaticRefreshableToken

pytestmark = pytest.mark.e2e


def _inject_module_source() -> str:
    """Source for the subprocess-side injection module.

    Wires a fake CallbackListener + fake browser + fake OAuth exchanger
    so the subprocess never opens a real browser or hits Slack's
    OAuth endpoint. The token store remains the REAL FileTokenStore so
    the captured tokens land in the real on-disk format.
    """
    return r"""
from kairix.connect import cli as _cli_mod
from kairix.connect.protocols import CallbackResult, CapturedTokens
from kairix.connect.oauth2.slack import SLACK_TOKEN_URI, SlackOAuth2Flow

class _StubListener:
    @property
    def redirect_uri(self): return "http://127.0.0.1:8080/oauth2callback"
    def wait_for_callback(self, timeout_s=120.0):
        return CallbackResult(code="e2e-slack-code-001", state=None)
    def close(self): pass

class _StubBrowser:
    def open(self, url): return True

def _stub_flow_factory(args):
    def _stub_exchanger(_c, code, _ru):
        return CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token=f"xoxb-e2e-{code}",
        )
    return SlackOAuth2Flow(
        workspace=args.workspace,
        client_id=args.client_id,
        client_secret=args.client_secret,
        browser=_StubBrowser(),
        token_exchanger=_stub_exchanger,
    )

_orig_main = _cli_mod.main
def patched_main(argv=None, *, deps=None):
    if deps is None:
        deps = _cli_mod.ConnectDeps(
            listener_factory=lambda _h, _p: _StubListener(),
            oauth2_flow_factory=_stub_flow_factory,
        )
    return _orig_main(argv, deps=deps)

_cli_mod.main = patched_main
"""


def test_composed_connect_slack_then_static_refresh_produces_valid_headers(tmp_path: Path) -> None:
    """End-to-end: connect → bot_token lands in file store → static-refresh produces auth headers."""
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    # Step 1: operator runs kairix connect slack
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import _inject; import kairix.cli; kairix.cli.main()",
            "connect",
            "slack",
            "--workspace",
            "test",
            "--client-id",
            "e2e-cid",
            "--client-secret",
            "e2e-csec",  # pragma: allowlist secret
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"connect failed: stdout={result.stdout} stderr={result.stderr}"
    # The success summary names the per-workspace canonical bot-token env var.
    assert "KAIRIX_CONNECTOR_SLACK_TEST_BOT_TOKEN" in result.stdout

    # Step 2: parse the captured secrets file (operator's KV at rest).
    captured: dict[str, str] = {}
    for line in secrets_file.read_text().splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            captured[name.strip()] = value.strip()
    assert captured["KAIRIX_CONNECTOR_SLACK_TEST_CLIENT_ID"] == "e2e-cid"
    assert captured["KAIRIX_CONNECTOR_SLACK_TEST_BOT_TOKEN"] == "xoxb-e2e-e2e-slack-code-001"
    # Slack does NOT persist a refresh-token / access-token — those fields
    # default to empty in CapturedTokens and the leaf-derivation helper
    # skips empty fields at write time.
    assert "KAIRIX_CONNECTOR_SLACK_TEST_REFRESH_TOKEN" not in captured
    assert "KAIRIX_CONNECTOR_SLACK_TEST_ACCESS_TOKEN" not in captured

    # Step 3: simulate a Slack connector picking up the bot_token and
    # constructing a StaticRefreshableToken. The connector's HTTP layer
    # calls .headers() on every API call.
    token = StaticRefreshableToken(token=captured["KAIRIX_CONNECTOR_SLACK_TEST_BOT_TOKEN"])
    headers = token.headers()
    assert headers == {"Authorization": "Bearer xoxb-e2e-e2e-slack-code-001"}
    # Slack bot tokens never expire — is_expired stays False forever.
    assert token.is_expired() is False
    # Multiple calls produce stable headers (no refresh cycle).
    assert token.headers() == headers
