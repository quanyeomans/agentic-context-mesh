"""E2E composed-path test for kairix connect (F48 + ADR-032 §"E2E").

Carries ``@pytest.mark.e2e`` and runs in CI Stage 4.5. Exercises the
full operator journey end-to-end:

  1. Operator downloads ``client_secret.json``.
  2. Operator runs ``python -m kairix.cli connect google-gmail
     --client-secret-path <path> --store=file``.
  3. The localhost listener captures a callback (test simulates this
     via a fake injected before argparse runs).
  4. The captured tokens land in the configured ``$KAIRIX_SECRETS_FILE``.
  5. The downstream :class:`kairix.connect.refresh.GoogleRefreshableToken`
     wired into the Drive/Calendar/Gmail connectors picks up the
     fresh refresh_token and produces a valid ``Authorization: Bearer ...``
     header via an injected refresh_fn (the fake Gmail API call).

This is the "composed production path" — config → factory → ingest →
query → assertion. The factory here is the connect CLI's
``ConnectDeps`` + the refresh module's ``GoogleRefreshableToken``;
the "ingest" is the token write; the "query" is the subsequent
refresh call producing valid auth headers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.connect.refresh import GoogleRefreshableToken, GoogleRefreshState

pytestmark = pytest.mark.e2e


def _make_client_secret(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "e2e-cid",
                    "client_secret": "e2e-csec",  # pragma: allowlist secret
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                },
            },
        ),
    )


def _inject_module_source() -> str:
    """Source for the subprocess-side injection module.

    Wires a fake CallbackListener + fake browser + fake OAuth exchanger
    so the subprocess never opens a real browser or hits Google's
    OAuth endpoint. The token store remains the REAL FileTokenStore so
    the captured tokens land in the real on-disk format.
    """
    return r"""
from kairix.connect import cli as _cli_mod
from kairix.connect.protocols import CallbackResult, CapturedTokens, ClientCredentials
from kairix.connect.oauth2.google import GoogleOAuth2Flow, GOOGLE_TOKEN_URI

class _StubListener:
    @property
    def redirect_uri(self): return "http://127.0.0.1:8080/oauth2callback"
    def wait_for_callback(self, timeout_s=120.0):
        return CallbackResult(code="e2e-code-001", state=None)
    def close(self): pass

class _StubBrowser:
    def open(self, url): return True

def _stub_flow_factory(args):
    area_map = {"google-gmail": "gmail", "google-drive": "google-drive", "google-calendar": "google-calendar"}
    def _stub_exchanger(_c, code, _ru):
        return CapturedTokens(
            refresh_token=f"e2e-refresh-{code}",
            access_token=f"e2e-access-{code}",
            token_uri=GOOGLE_TOKEN_URI,
        )
    return GoogleOAuth2Flow(
        service_area=area_map[args.subcommand],
        client_secret_path=args.client_secret_path,
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


def test_composed_connect_then_refresh_produces_valid_headers(tmp_path: Path) -> None:
    """End-to-end: connect → tokens land in file store → refresh produces auth headers."""
    cs = tmp_path / "client_secret.json"
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    _make_client_secret(cs)
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    # Step 1: operator runs kairix connect google-gmail
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import _inject; import kairix.cli; kairix.cli.main()",
            "connect",
            "google-gmail",
            "--client-secret-path",
            str(cs),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"connect failed: stdout={result.stdout} stderr={result.stderr}"
    # The success summary names the canonical secrets.
    assert "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN" in result.stdout

    # Step 2: parse the captured secrets file (operator's KV at rest).
    captured: dict[str, str] = {}
    for line in secrets_file.read_text().splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            captured[name.strip()] = value.strip()
    assert captured["KAIRIX_CONNECTOR_GMAIL_CLIENT_ID"] == "e2e-cid"
    assert captured["KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN"] == "e2e-refresh-e2e-code-001"

    # Step 3: simulate a Drive/Gmail connector picking up those tokens
    # and constructing a GoogleRefreshableToken. The fake refresh_fn
    # confirms the refresh dance can be re-run from the captured
    # refresh_token without hitting Google.
    refresh_calls: list[tuple[str, str | None]] = []

    def fake_refresh(state: GoogleRefreshState, existing: str | None) -> tuple[str, float]:
        refresh_calls.append((state.refresh_token, existing))
        return "live-access-from-refresh", 9999999999.0

    state = GoogleRefreshState(
        client_id=captured["KAIRIX_CONNECTOR_GMAIL_CLIENT_ID"],
        client_secret=captured["KAIRIX_CONNECTOR_GMAIL_CLIENT_SECRET"],
        refresh_token=captured["KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
    )
    token = GoogleRefreshableToken(state=state, refresh_fn=fake_refresh)
    # The connector calls .headers() on every API call.
    headers = token.headers()
    assert headers == {"Authorization": "Bearer live-access-from-refresh"}
    assert refresh_calls == [("e2e-refresh-e2e-code-001", None)]
    # Subsequent calls within the expiry window don't trigger another refresh.
    headers_again = token.headers()
    assert headers_again == headers
    assert len(refresh_calls) == 1  # still just one refresh
