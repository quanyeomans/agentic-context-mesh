"""Integration: kairix connect slack via subprocess against fakes.

Runs the real ``python -m kairix.cli connect slack ...`` entry point in
a subprocess. The subprocess loads ``ConnectDeps`` from a small
sitecustomize that wires the SlackOAuth2Flow with an injected
exchanger so no real Slack HTTP call happens.

F47 alignment: composes the entry-point end-to-end without
monkeypatching the kairix module in this process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _inject_module_source() -> str:
    """Python source that swaps ``ConnectDeps`` defaults for the subprocess.

    Mirrors the Google integration test's shape: the subprocess imports
    this module BEFORE ``kairix.cli`` parses argv; the injection
    replaces ``kairix.connect.cli.main`` with a wrapper that constructs
    ConnectDeps wired to a SlackOAuth2Flow with a recording exchanger.
    """
    return r"""
from kairix.connect import cli as _cli_mod
from kairix.connect.protocols import CallbackResult, CapturedTokens
from kairix.connect.oauth2.slack import SLACK_TOKEN_URI, SlackOAuth2Flow

class _StubListener:
    @property
    def redirect_uri(self): return "http://127.0.0.1:8080/oauth2callback"
    def wait_for_callback(self, timeout_s=120.0):
        return CallbackResult(code="integration-slack-code", state=None)
    def close(self): pass

class _StubBrowser:
    def open(self, url): return True

def _stub_flow_factory(args):
    def _stub_exchanger(_c, code, _ru):
        return CapturedTokens(
            refresh_token="",
            access_token="",
            token_uri=SLACK_TOKEN_URI,
            bot_token=f"xoxb-int-{code}",
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


def test_connect_slack_subprocess_writes_per_workspace_canonical_secrets(tmp_path: Path) -> None:
    """Full subprocess invocation writes the per-workspace canonical env vars."""
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable,
        "-c",
        "import _inject; import kairix.cli; kairix.cli.main()",
        "connect",
        "slack",
        "--workspace",
        "alpha",
        "--client-id",
        "int-slack-cid",
        "--client-secret",
        "int-slack-csec",  # pragma: allowlist secret
    ]
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"stdout:{result.stdout}\nstderr:{result.stderr}"
    assert secrets_file.exists()
    content = secrets_file.read_text()
    # pragma: allowlist secret — every line below is a fake test fixture
    bt_line = "KAIRIX_CONNECTOR_SLACK_ALPHA_BOT_TOKEN=xoxb-int-integration-slack-code"  # pragma: allowlist secret
    cid_line = "KAIRIX_CONNECTOR_SLACK_ALPHA_CLIENT_ID=int-slack-cid"  # pragma: allowlist secret
    cs_line = "KAIRIX_CONNECTOR_SLACK_ALPHA_CLIENT_SECRET=int-slack-csec"  # pragma: allowlist secret
    assert bt_line in content, f"expected bot-token line in {content!r}"
    assert cid_line in content, f"expected client-id line in {content!r}"
    assert cs_line in content, f"expected client-secret line in {content!r}"
    # Slack does NOT write refresh-token / access-token (those fields are empty).
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA_REFRESH_TOKEN" not in content
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA_ACCESS_TOKEN" not in content


def test_connect_slack_two_workspaces_co_resident_in_same_file(tmp_path: Path) -> None:
    """Two slack runs with different workspaces leave both per-workspace bot-tokens in the file."""
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    for ws in ("alpha", "coach"):
        cmd = [
            sys.executable,
            "-c",
            "import _inject; import kairix.cli; kairix.cli.main()",
            "connect",
            "slack",
            "--workspace",
            ws,
            "--client-id",
            f"cid-{ws}",
            "--client-secret",
            f"csec-{ws}",  # pragma: allowlist secret
        ]
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    content = secrets_file.read_text()
    # Both workspaces present, distinct lines per workspace.
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA_BOT_TOKEN=" in content
    assert "KAIRIX_CONNECTOR_SLACK_COACH_BOT_TOKEN=" in content
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA_CLIENT_ID=cid-alpha" in content
    assert "KAIRIX_CONNECTOR_SLACK_COACH_CLIENT_ID=cid-coach" in content
