"""Integration: kairix connect github-app via subprocess against fakes.

Runs the real ``python -m kairix.cli connect github-app`` entry point
in a subprocess; the subprocess loads ``ConnectDeps`` from a tiny
injection module that wires fakes for the listener + JWT exchanger.
F47 alignment: composes the entry point end-to-end without
monkeypatching the kairix module in this process.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


_FAKE_PEM = (  # pragma: allowlist secret
    "-----BEGIN RSA PRIVATE KEY-----\nFAKE-PEM-BODY-FOR-INTEGRATION-TESTING\n-----END RSA PRIVATE KEY-----\n"
)


def _write_pem(path: Path) -> Path:
    path.write_text(_FAKE_PEM)
    return path


def _inject_module_source() -> str:
    """Subprocess-side bootstrap that patches the github-app flow factory.

    Wires:
      * a fake CallbackListener returning installation_id=70000
      * a fake browser (no-op .open(url))
      * a fake JWT exchanger returning a deterministic installation token
    The token store remains the REAL FileTokenStore so we assert on the
    on-disk layout matching the canonical names.
    """
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
            params={"installation_id": "70000", "setup_action": "install"},
        )
    def close(self): pass

class _StubBrowser:
    def open(self, url): return True

def _stub_flow_factory(args):
    def _stub_exchanger(captured_app_id, _pem, installation_id):
        return f"int-token-{captured_app_id}-{installation_id}"
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


def test_github_app_subprocess_writes_canonical_secrets(tmp_path: Path) -> None:
    """Full subprocess invocation writes the canonical secrets file."""
    pem = _write_pem(tmp_path / "app.pem")
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
        "github-app",
        "--app-id",
        "42",
        "--private-key-path",
        str(pem),
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
    assert secrets_file.exists(), "expected secrets file to be written"
    content = secrets_file.read_text()
    # pragma: allowlist secret — every line below is a fake test fixture
    expected_access = "KAIRIX_CONNECTOR_GITHUB_ACCESS_TOKEN=int-token-42-70000"  # pragma: allowlist secret
    expected_install_id = "KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID=70000"
    assert expected_access in content, f"expected access token line, got:\n{content}"
    assert expected_install_id in content, f"expected installation-id line, got:\n{content}"
    # Review finding H2: the CLI must write the FULL App-mode set the
    # connector's credential resolver reads — app-id + the PEM private
    # key (newline-safe encoded onto one line, still greppable) — not
    # the repurposed client-id/client-secret slot names.
    assert "KAIRIX_CONNECTOR_GITHUB_APP_ID=42" in content, f"expected app-id line, got:\n{content}"
    pem_lines = [line for line in content.splitlines() if line.startswith("KAIRIX_CONNECTOR_GITHUB_APP_PRIVATE_KEY=")]
    assert len(pem_lines) == 1, f"expected one app-private-key line, got:\n{content}"
    assert "BEGIN RSA PRIVATE KEY" in pem_lines[0]
    assert "KAIRIX_CONNECTOR_GITHUB_CLIENT_ID=" not in content
    # Every line stays KEY=VALUE parseable — the raw multi-line PEM
    # corrupted the bundle before the encoding landed.
    for line in content.splitlines():
        assert "=" in line, f"unparseable bundle line: {line!r}"
    # The success summary on stdout lists the canonical names.
    assert "KAIRIX_CONNECTOR_GITHUB" in result.stdout
    assert "INSTALLATION_ID" in result.stdout
    assert "KAIRIX_CONNECTOR_GITHUB_APP_ID" in result.stdout


def test_github_app_subprocess_emits_summary_to_stdout(tmp_path: Path) -> None:
    """The success summary is printed to stdout (not stderr)."""
    pem = _write_pem(tmp_path / "app.pem")
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
        "github-app",
        "--app-id",
        "42",
        "--private-key-path",
        str(pem),
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
    # Strengthened: the github-app subcommand line + backend name + each
    # canonical leaf must all be present in stdout so a regression that
    # silences any half is caught.
    assert "kairix connect github-app: ok" in result.stdout
    assert "backend: file" in result.stdout
    assert "KAIRIX_CONNECTOR_GITHUB_ACCESS_TOKEN" in result.stdout
    assert "KAIRIX_CONNECTOR_GITHUB_INSTALLATION_ID" in result.stdout
    # The stderr is empty on success.
    assert result.stderr == "", f"expected empty stderr on success, got: {result.stderr!r}"


def test_github_app_subprocess_missing_pem_returns_nonzero(tmp_path: Path) -> None:
    """A missing --private-key-path returns non-zero + F21-shaped stderr."""
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
        "github-app",
        "--app-id",
        "42",
        "--private-key-path",
        str(tmp_path / "does-not-exist.pem"),
    ]
    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode != 0
    assert "private key" in result.stderr.lower()
    assert "fix:" in result.stderr and "next:" in result.stderr and "run:" in result.stderr
