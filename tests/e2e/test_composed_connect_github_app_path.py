"""E2E composed-path test for kairix connect github-app (F48 + ADR-032 §"E2E").

Carries ``@pytest.mark.e2e`` and runs in CI Stage 4.5. Exercises the
full operator journey end-to-end:

  1. Operator generates an App at github.com/settings/apps + downloads
     the PEM private key.
  2. Operator runs ``python -m kairix.cli connect github-app --app-id <id>
     --private-key-path <path> --store=file``.
  3. The localhost listener captures the App install callback with the
     ``installation_id`` query param (test simulates this via a fake
     injected before argparse runs).
  4. The captured installation token + installation_id land in the
     configured ``$KAIRIX_SECRETS_FILE``.
  5. The downstream :class:`kairix.connect.refresh.GitHubAppRefreshableToken`
     wired into the GitHub connector picks up the App id + PEM +
     installation_id and produces a valid ``Authorization: Bearer ...``
     header via an injected token_exchanger (the fake JWT-sign +
     installation-token-exchange chain).

This is the "composed production path" — config → factory → ingest →
query → assertion. The factory here is the connect CLI's
``ConnectDeps`` + the refresh module's ``GitHubAppRefreshableToken``;
the "ingest" is the token write; the "query" is the subsequent
refresh call producing valid auth headers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairix.connect.refresh import GitHubAppRefreshableToken

pytestmark = pytest.mark.e2e


_FAKE_PEM = (  # pragma: allowlist secret
    "-----BEGIN RSA PRIVATE KEY-----\nFAKE-PEM-BODY-FOR-E2E-TESTING-NOT-A-REAL-KEY\n-----END RSA PRIVATE KEY-----\n"
)


def _write_pem(path: Path) -> Path:
    path.write_text(_FAKE_PEM)
    return path


def _inject_module_source() -> str:
    """Subprocess-side injection wiring fakes for the github-app E2E path."""
    return r"""
from pathlib import Path
from kairix.connect import cli as _cli_mod
from kairix.connect.protocols import CallbackResult
from kairix.connect.oauth2.github_app import GitHubAppFlow

class _StubListener:
    @property
    def redirect_uri(self): return "http://127.0.0.1:8080/oauth2callback"
    def wait_for_callback(self, timeout_s=120.0):
        return CallbackResult(
            code="ignored",
            state=None,
            params={"installation_id": "e2e-installation-99", "setup_action": "install"},
        )
    def close(self): pass

class _StubBrowser:
    def open(self, url): return True

def _stub_flow_factory(args):
    def _stub_exchanger(captured_app_id, _pem, installation_id):
        # Deterministic shape so the assertions can pin exact values.
        return f"e2e-installation-token-{captured_app_id}-{installation_id}"
    return GitHubAppFlow(
        app_id=args.app_id,
        private_key_path=args.private_key_path,
        app_slug=args.app_slug,
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


def test_composed_github_app_connect_then_refresh_produces_valid_headers(tmp_path: Path) -> None:
    """End-to-end: connect → tokens land in file → refresh produces auth headers."""
    pem = _write_pem(tmp_path / "app.pem")
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    # Step 1: operator runs kairix connect github-app
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import _inject; import kairix.cli; kairix.cli.main()",
            "connect",
            "github-app",
            "--app-id",
            "12345",
            "--private-key-path",
            str(pem),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, f"connect failed: stdout={result.stdout} stderr={result.stderr}"
    # Strengthened: the success summary names BOTH the access-token AND
    # the installation-id canonical leaves so a regression that drops
    # the metadata write is caught.
    assert "KAIRIX_CONNECTOR_GITHUB_ACCESS_TOKEN" in result.stdout
    assert "KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID" in result.stdout

    # Step 2: parse the captured secrets file (operator's KV at rest)
    captured: dict[str, str] = {}
    for line in secrets_file.read_text().splitlines():
        if "=" in line:
            name, _, value = line.partition("=")
            captured[name.strip()] = value.strip()
    assert captured["KAIRIX_CONNECTOR_GITHUB_ACCESS_TOKEN"] == "e2e-installation-token-12345-e2e-installation-99"
    assert captured["KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID"] == "e2e-installation-99"

    # Step 3: simulate the GitHub connector picking up those secrets +
    # the on-disk PEM, and constructing a GitHubAppRefreshableToken.
    # The fake token_exchanger confirms the refresh dance can be re-run
    # from the captured App id + installation_id without hitting GitHub.
    refresh_calls: list[tuple[str, str]] = []

    def fake_exchanger(app_id: str, _pem: str, installation_id: str) -> tuple[str, float]:
        refresh_calls.append((app_id, installation_id))
        return "live-installation-token-from-refresh", 9999999999.0

    refreshable = GitHubAppRefreshableToken(
        app_id="12345",
        private_key_pem=_FAKE_PEM,
        installation_id=captured["KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID"],
        token_exchanger=fake_exchanger,
    )
    # The connector calls .headers() on every API call.
    headers = refreshable.headers()
    assert headers == {"Authorization": "Bearer live-installation-token-from-refresh"}
    assert refresh_calls == [("12345", "e2e-installation-99")]
    # Subsequent calls within the rotation budget don't trigger another refresh.
    headers_again = refreshable.headers()
    assert headers_again == headers
    assert len(refresh_calls) == 1


def test_composed_github_app_connect_writes_no_refresh_token(tmp_path: Path) -> None:
    """GitHub App flow doesn't write a refresh-token line (the JWT signing key is the long-lived credential).

    Strengthened: confirms the file_store's empty-value skip is working
    for the github-app flow — a regression that writes a blank
    REFRESH_TOKEN line would corrupt the canonical naming.
    """
    pem = _write_pem(tmp_path / "app.pem")
    secrets_file = tmp_path / "kairix.env"
    inject = tmp_path / "_inject.py"
    inject.write_text(_inject_module_source())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import _inject; import kairix.cli; kairix.cli.main()",
            "connect",
            "github-app",
            "--app-id",
            "12345",
            "--private-key-path",
            str(pem),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    content = secrets_file.read_text()
    # No empty refresh-token line should be written
    assert "KAIRIX_CONNECTOR_GITHUB_REFRESH_TOKEN=" not in content
    # The success summary on stdout should likewise not list a refresh-token leaf
    assert "REFRESH_TOKEN" not in result.stdout
