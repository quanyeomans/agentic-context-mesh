"""Unit-level CLI coverage for ``kairix connect github-app``.

Exercises the dispatcher via :func:`main` with ``ConnectDeps`` injected
— pinned to the github-app subcommand path so the Google tests in
``test_connect_cli.py`` stay independent.

Per the test discipline brief: every test that builds a GitHubAppFlow
passes ``browser=FakeBrowserLauncher()`` explicitly; the
``KAIRIX_CONNECT_DISABLE_BROWSER`` kill-switch is defense-in-depth
only — these tests inject the fake browser at construction.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest

from kairix.connect.cli import (
    SUBCOMMAND_REGISTRY,
    ConnectDeps,
    main,
)
from kairix.connect.oauth2.github_app import GITHUB_APP_SERVICE_AREA, GitHubAppFlow
from kairix.connect.protocols import (
    CallbackResult,
    OAuth2Flow,
    TokenStoreUnauthorizedError,
)
from tests.fakes import (
    FakeBrowserLauncher,
    FakeCallbackListener,
    FakeTokenStore,
)

pytestmark = pytest.mark.unit


_FAKE_PEM = (  # pragma: allowlist secret
    "-----BEGIN RSA PRIVATE KEY-----\nFAKE-PEM-BODY-FOR-CLI-TESTING-NOT-A-REAL-KEY\n-----END RSA PRIVATE KEY-----\n"
)


def _write_pem(path: Path) -> Path:
    path.write_text(_FAKE_PEM)
    return path


def _make_oauth2_factory(
    *,
    install_id: str = "98765",
    token: str = "installation-token-xyz",
    captured_app_slugs: list[str] | None = None,
) -> tuple[Any, FakeCallbackListener]:
    """Build a oauth2_flow_factory that returns a fake-driven GitHubAppFlow.

    The flow uses an injected ``FakeBrowserLauncher`` + a closure
    ``token_exchanger`` so no real browser opens and no real JWT-sign
    happens. The listener is returned for the caller to wire into
    ``listener_factory``.
    """
    listener = FakeCallbackListener(
        callback=CallbackResult(
            code="ignored",
            state=None,
            params={"installation_id": install_id, "setup_action": "install"},
        ),
    )
    browser = FakeBrowserLauncher()

    def oauth2_factory(args: argparse.Namespace) -> OAuth2Flow:
        if captured_app_slugs is not None:
            captured_app_slugs.append(args.app_slug)
        return GitHubAppFlow(
            app_id=args.app_id,
            private_key_path=args.private_key_path,
            app_slug=args.app_slug,
            browser=browser,
            token_exchanger=lambda *_: token,
        )

    return oauth2_factory, listener


def _deps_with_install_id(
    *,
    install_id: str = "98765",
    token: str = "installation-token-xyz",
    store: FakeTokenStore | None = None,
    stderr: io.StringIO | None = None,
    stdout: io.StringIO | None = None,
) -> ConnectDeps:
    """Build a ConnectDeps wiring the github-app factory with fakes."""
    factory, listener = _make_oauth2_factory(install_id=install_id, token=token)
    return ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: store if store is not None else FakeTokenStore(),
        stdout=stdout if stdout is not None else io.StringIO(),
        stderr=stderr if stderr is not None else io.StringIO(),
    )


def test_subcommand_registry_contains_github_app() -> None:
    """The dispatch dict carries the github-app subcommand mapping."""
    assert "github-app" in SUBCOMMAND_REGISTRY
    spec = SUBCOMMAND_REGISTRY["github-app"]
    assert spec.service_area == GITHUB_APP_SERVICE_AREA == "github"


def test_subcommand_registry_google_entries_preserved() -> None:
    """Google subcommands stay in the registry alongside github-app + slack."""
    google_areas = {"gmail", "google-drive", "google-calendar"}
    google_entries = {name for name, spec in SUBCOMMAND_REGISTRY.items() if spec.service_area in google_areas}
    assert google_entries == {"google-gmail", "google-drive", "google-calendar"}


def test_subcommand_registry_github_app_instance_reader_returns_none() -> None:
    """github-app is a singleton — the instance slot is None (no per-tenant suffix)."""
    spec = SUBCOMMAND_REGISTRY["github-app"]
    namespace = argparse.Namespace()
    assert spec.instance_reader(namespace) is None


def test_cli_github_app_happy_path_returns_zero(tmp_path: Path) -> None:
    """github-app happy path writes one store call + exits zero."""
    pem = _write_pem(tmp_path / "app.pem")
    store = FakeTokenStore()
    stdout = io.StringIO()
    deps = _deps_with_install_id(install_id="98765", token="ghs_token_abc", store=store, stdout=stdout)
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 0, f"expected 0, got {rc}; stdout={stdout.getvalue()!r}"
    assert len(store.writes) == 1
    write = store.writes[0]
    assert write["area"] == "github"
    assert write["scope"] == "connector"
    assert write["instance"] is None
    assert write["tokens"].access_token == "ghs_token_abc"
    assert write["tokens"].metadata == {"installation-id": "98765"}


def test_cli_github_app_summary_lists_installation_id_leaf(tmp_path: Path) -> None:
    """The success summary mentions the installation-id canonical name."""
    pem = _write_pem(tmp_path / "app.pem")
    stdout = io.StringIO()
    deps = _deps_with_install_id(stdout=stdout)
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 0
    out = stdout.getvalue()
    assert "KAIRIX_CONNECTOR_GITHUB" in out
    assert "INSTALLATION_ID" in out


def test_cli_github_app_missing_app_id_argparse_rejects(tmp_path: Path) -> None:
    """argparse rejects --app-id-less invocations at parse time."""
    pem = _write_pem(tmp_path / "app.pem")
    with pytest.raises(SystemExit) as exc_info:
        main(["github-app", "--private-key-path", str(pem)])
    # argparse usage-error exit code
    assert exc_info.value.code == 2


def test_cli_github_app_missing_private_key_argparse_rejects() -> None:
    """argparse rejects invocations missing --private-key-path."""
    with pytest.raises(SystemExit) as exc_info:
        main(["github-app", "--app-id", "42"])
    assert exc_info.value.code == 2


def test_cli_github_app_missing_pem_file_returns_nonzero(tmp_path: Path) -> None:
    """A --private-key-path pointing at a non-existent file returns non-zero with F21 markers."""
    stderr = io.StringIO()
    deps = _deps_with_install_id(stderr=stderr)
    rc = main(
        [
            "github-app",
            "--app-id",
            "42",
            "--private-key-path",
            str(tmp_path / "missing.pem"),
        ],
        deps=deps,
    )
    assert rc == 1
    err = stderr.getvalue()
    assert "private key not found" in err.lower() or "private key" in err.lower()
    assert "fix:" in err and "next:" in err and "run:" in err


def test_cli_github_app_token_store_failure_returns_nonzero(tmp_path: Path) -> None:
    """Token store rejecting the write surfaces the F21 error + non-zero exit."""
    pem = _write_pem(tmp_path / "app.pem")
    stderr = io.StringIO()
    failing_store = FakeTokenStore(
        raises=TokenStoreUnauthorizedError(
            "fake store: KV write rejected. "
            "fix: confirm Secrets Officer role. next: az role assignment. run: kairix connect github-app",
        ),
    )
    deps = _deps_with_install_id(store=failing_store, stderr=stderr)
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 1
    err = stderr.getvalue()
    assert "KV write rejected" in err or "rejected" in err
    assert "fix:" in err and "next:" in err and "run:" in err


def test_cli_github_app_subcommand_uses_default_app_slug(tmp_path: Path) -> None:
    """The default --app-slug ('kairix-bot') threads through to the install URL."""
    pem = _write_pem(tmp_path / "app.pem")
    captured_slugs: list[str] = []
    factory, listener = _make_oauth2_factory(captured_app_slugs=captured_slugs)
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: FakeTokenStore(),
    )
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 0
    assert captured_slugs == ["kairix-bot"]


def test_cli_github_app_custom_app_slug_threads_through(tmp_path: Path) -> None:
    """--app-slug overrides the default install-URL slug."""
    pem = _write_pem(tmp_path / "app.pem")
    captured_slugs: list[str] = []
    factory, listener = _make_oauth2_factory(captured_app_slugs=captured_slugs)
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: FakeTokenStore(),
    )
    rc = main(
        [
            "github-app",
            "--app-id",
            "42",
            "--private-key-path",
            str(pem),
            "--app-slug",
            "my-custom-app",
        ],
        deps=deps,
    )
    assert rc == 0
    assert captured_slugs == ["my-custom-app"]


def test_cli_github_app_empty_callback_install_id_returns_nonzero(tmp_path: Path) -> None:
    """Listener returning no installation_id surfaces the F21 error + rc=1.

    Drives through ``main`` with a real ``GitHubAppFlow`` built by an
    explicit factory; the listener returns an empty callback so the
    flow raises ValueError about the missing installation_id. Proves
    the dispatcher routes errors from the flow back to stderr + rc=1.
    """
    pem = _write_pem(tmp_path / "app.pem")
    listener = FakeCallbackListener(
        callback=CallbackResult(code="", state=None, params={}),
    )
    browser = FakeBrowserLauncher()

    def factory(args: argparse.Namespace) -> OAuth2Flow:
        return GitHubAppFlow(
            app_id=args.app_id,
            private_key_path=args.private_key_path,
            app_slug=args.app_slug,
            browser=browser,
            token_exchanger=lambda *_: "tok",
        )

    stderr = io.StringIO()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: FakeTokenStore(),
        stderr=stderr,
    )
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 1
    assert "installation_id" in stderr.getvalue()


def test_cli_unknown_subcommand_via_argparse_choices(tmp_path: Path) -> None:
    """argparse rejects unknown subcommands at parse time (exits via SystemExit)."""
    with pytest.raises(SystemExit) as exc_info:
        main(["unknown-service", "--client-secret-path", "/tmp/x"])
    assert exc_info.value.code == 2  # argparse usage-error exit code


def test_cli_github_app_default_listener_factory_oserror_returns_nonzero(tmp_path: Path) -> None:
    """Listener-factory OSError on the github-app path returns non-zero."""
    pem = _write_pem(tmp_path / "app.pem")
    stderr = io.StringIO()

    def boom(_host: str, _port: int) -> Any:
        raise OSError(
            "kairix connect: no free port. fix: pass --port. next: lsof. run: kairix connect github-app --port 9090",
        )

    factory, _listener = _make_oauth2_factory()
    deps = ConnectDeps(
        listener_factory=boom,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(
        ["github-app", "--app-id", "42", "--private-key-path", str(pem)],
        deps=deps,
    )
    assert rc == 1
    assert "no free port" in stderr.getvalue()
