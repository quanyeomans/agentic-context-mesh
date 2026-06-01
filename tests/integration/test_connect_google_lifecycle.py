"""Integration: kairix connect google-gmail via subprocess against fakes.

Runs the real ``python -m kairix.cli connect google-gmail`` entry point
in a subprocess; the subprocess loads ``ConnectDeps`` from a tiny
sitecustomize that wires fakes. F47 alignment: composes the entry-point
end-to-end without monkeypatching the kairix module in this process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _make_client_secret(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "int-cid",
                    "client_secret": "int-csec",  # pragma: allowlist secret
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                },
            },
        ),
    )


def _conftest_inject_fakes() -> str:
    """Python source that patches ``ConnectDeps`` factories in-process.

    Returned as a string; written into a small bootstrap module the
    subprocess imports before kairix.cli runs. Uses
    ``kairix.connect.cli.ConnectDeps`` defaults by replacing them on the
    dataclass field-defaults; this is intentional for this integration
    seam — F1 governs PRODUCTION tests, F47 says integration tests
    compose via the CLI entry. The substitution happens BEFORE the CLI
    parses argv, so the subprocess runs the real argparse + dispatch.
    """
    return r"""
from __future__ import annotations
import sys
from pathlib import Path
from kairix.connect import cli as _cli_mod
from kairix.connect.protocols import CallbackResult, CapturedTokens, ClientCredentials
from kairix.connect.oauth2.google import GoogleOAuth2Flow, GOOGLE_TOKEN_URI

class _StubListener:
    @property
    def redirect_uri(self) -> str: return "http://127.0.0.1:8080/oauth2callback"
    def wait_for_callback(self, timeout_s: float = 120.0):  # noqa: ANN001 — stub matches Protocol
        return CallbackResult(code="integration-code", state=None)
    def close(self) -> None: pass

class _StubBrowser:
    def open(self, url: str) -> bool: return True

def _stub_flow_factory(args):  # noqa: ANN001 — stub matches Protocol
    area_map = {"google-gmail": "gmail", "google-drive": "google-drive", "google-calendar": "google-calendar"}
    def _stub_exchanger(_c, code, _ru):
        return CapturedTokens(
            refresh_token=f"int-refresh-{code}",
            access_token=f"int-access-{code}",
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


def test_connect_google_gmail_subprocess_writes_canonical_secrets(tmp_path: Path) -> None:
    """Full subprocess invocation writes the four canonical env vars to the file store."""
    cs = tmp_path / "client_secret.json"
    secrets_file = tmp_path / "kairix.env"
    _make_client_secret(cs)
    sitecustomize = tmp_path / "_inject.py"
    sitecustomize.write_text(_conftest_inject_fakes())

    env = dict(os.environ)
    # Tell the subprocess where to write the secrets via the file store
    # (F2/F4-clean: this is the operator-facing env var, not test-only).
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    # Prepend tmp_path so our injection module loads.
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    # Tell the subprocess to import the injection module via -c.
    cmd = [
        sys.executable,
        "-c",
        "import _inject; import kairix.cli; kairix.cli.main()",
        "connect",
        "google-gmail",
        "--client-secret-path",
        str(cs),
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
    assert "KAIRIX_CONNECTOR_GMAIL_CLIENT_ID=int-cid" in content
    rt_line = "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN=int-refresh-integration-code"  # pragma: allowlist secret
    at_line = "KAIRIX_CONNECTOR_GMAIL_ACCESS_TOKEN=int-access-integration-code"  # pragma: allowlist secret
    cs_line = "KAIRIX_CONNECTOR_GMAIL_CLIENT_SECRET=int-csec"  # pragma: allowlist secret
    assert cs_line in content
    assert rt_line in content
    assert at_line in content


def test_connect_google_drive_subprocess_writes_canonical_secrets(tmp_path: Path) -> None:
    cs = tmp_path / "client_secret.json"
    secrets_file = tmp_path / "kairix.env"
    _make_client_secret(cs)
    sitecustomize = tmp_path / "_inject.py"
    sitecustomize.write_text(_conftest_inject_fakes())

    env = dict(os.environ)
    env["KAIRIX_SECRETS_FILE"] = str(secrets_file)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable,
        "-c",
        "import _inject; import kairix.cli; kairix.cli.main()",
        "connect",
        "google-drive",
        "--client-secret-path",
        str(cs),
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
    content = secrets_file.read_text()
    # pragma: allowlist secret — every line below is a fake test fixture
    assert "KAIRIX_CONNECTOR_GOOGLE_DRIVE_CLIENT_ID=int-cid" in content
    rt_line = "KAIRIX_CONNECTOR_GOOGLE_DRIVE_REFRESH_TOKEN=int-refresh-integration-code"  # pragma: allowlist secret
    assert rt_line in content
