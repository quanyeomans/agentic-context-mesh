"""Unit-level coverage for kairix.connect.cli.

Exercises the dispatcher via :func:`main` with ``ConnectDeps`` injected
— no subprocess, no real HTTP listener, no real OAuth provider. The
integration + E2E tests cover the subprocess path.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from kairix.connect.cli import (
    ConnectDeps,
    main,
)
from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI
from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from kairix.connect.store.azure_kv_store import AzureKeyVaultTokenStore
from kairix.connect.store.file_store import FileTokenStore
from kairix.connect.store.stdout_store import StdoutTokenStore
from tests.fakes import (
    FakeCallbackListener,
    FakeTokenStore,
)

pytestmark = pytest.mark.unit


def _make_client_secret(path: Path) -> Path:
    payload = {
        "installed": {
            "client_id": "test-cid",
            "client_secret": "test-csec",  # pragma: allowlist secret
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": GOOGLE_TOKEN_URI,
        },
    }
    path.write_text(json.dumps(payload))
    return path


class _FakeFlow:
    """Pinned flow that skips the listener/browser plumbing entirely."""

    def __init__(self, *, tokens: CapturedTokens, client: ClientCredentials) -> None:
        self.service_area = "gmail"
        self.scopes = ()
        self._tokens = tokens
        self._client = client

    def discover_client_credentials(self) -> ClientCredentials:
        return self._client

    def authorize(self, *, listener: Any) -> CapturedTokens:
        return self._tokens


def _ok_deps(
    *,
    listener: Any | None = None,
    flow: Any | None = None,
    store: Any | None = None,
    stdout: io.StringIO | None = None,
    stderr: io.StringIO | None = None,
) -> ConnectDeps:
    """Construct a ConnectDeps wired to the supplied fakes."""
    real_stdout = stdout if stdout is not None else io.StringIO()
    real_stderr = stderr if stderr is not None else io.StringIO()
    real_listener = listener if listener is not None else FakeCallbackListener()
    real_store = store if store is not None else FakeTokenStore()
    if flow is None:
        flow = _FakeFlow(
            tokens=CapturedTokens(
                refresh_token="rt",
                access_token="at",
                token_uri=GOOGLE_TOKEN_URI,
            ),
            client=ClientCredentials(client_id="cid", client_secret="csec"),
        )
    return ConnectDeps(
        listener_factory=lambda _h, _p: real_listener,
        oauth2_flow_factory=lambda _cmd, _path, _port: flow,
        token_store_factory=lambda _spec: real_store,
        stdout=real_stdout,
        stderr=real_stderr,
    )


def test_cli_happy_path_returns_zero(tmp_path: Path) -> None:
    """End-to-end success — exit code 0, summary printed to stdout."""
    cs = _make_client_secret(tmp_path / "cs.json")
    stdout = io.StringIO()
    store = FakeTokenStore()
    deps = _ok_deps(store=store, stdout=stdout)
    rc = main(
        ["google-gmail", "--client-secret-path", str(cs)],
        deps=deps,
    )
    assert rc == 0
    assert "ok" in stdout.getvalue()
    assert "KAIRIX_CONNECTOR_GMAIL_REFRESH_TOKEN" in stdout.getvalue()
    assert len(store.writes) == 1
    assert store.writes[0]["area"] == "gmail"


def test_cli_consent_denied_returns_nonzero(tmp_path: Path) -> None:
    """A denied consent surfaces as exit 1 with the F21 hint on stderr."""
    cs = _make_client_secret(tmp_path / "cs.json")
    listener = FakeCallbackListener(denied=True, denied_message="user cancelled")
    stderr = io.StringIO()

    # Override the flow with one that DOES call the listener so the
    # CallbackDeniedError propagates.
    class _RealCallFlow:
        service_area = "gmail"
        scopes = ()

        def discover_client_credentials(self) -> ClientCredentials:
            return ClientCredentials(client_id="x", client_secret="y")

        def authorize(self, *, listener: Any) -> CapturedTokens:
            listener.wait_for_callback(timeout_s=1.0)  # raises
            raise AssertionError("should not reach here")

    deps_real = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=lambda _cmd, _path, _port: _RealCallFlow(),
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(["google-gmail", "--client-secret-path", str(cs)], deps=deps_real)
    assert rc == 1
    assert "consent" in stderr.getvalue() or "denied" in stderr.getvalue()


def test_cli_timeout_returns_nonzero(tmp_path: Path) -> None:
    """Listener timeout surfaces as exit 1."""
    cs = _make_client_secret(tmp_path / "cs.json")
    listener = FakeCallbackListener(timeout=True)
    stderr = io.StringIO()

    class _RealCallFlow:
        service_area = "gmail"
        scopes = ()

        def discover_client_credentials(self) -> ClientCredentials:
            return ClientCredentials(client_id="x", client_secret="y")

        def authorize(self, *, listener: Any) -> CapturedTokens:
            listener.wait_for_callback(timeout_s=1.0)
            raise AssertionError("should not reach")

    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=lambda _cmd, _path, _port: _RealCallFlow(),
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(["google-gmail", "--client-secret-path", str(cs)], deps=deps)
    assert rc == 1
    assert "timeout" in stderr.getvalue().lower() or "callback" in stderr.getvalue().lower()


def test_cli_missing_client_secret_returns_nonzero(tmp_path: Path) -> None:
    """A nonexistent client_secret_path surfaces as exit 1."""
    stderr = io.StringIO()
    missing = tmp_path / "nope.json"

    # Need a flow that actually opens the file (the default factory does
    # via GoogleOAuth2Flow). Use it here.
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: FakeCallbackListener(),
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(["google-gmail", "--client-secret-path", str(missing)], deps=deps)
    assert rc == 1
    assert "not found" in stderr.getvalue()


def _capture_store_from_main(tmp_path: Path, store_arg: str) -> tuple[int, object | None]:
    """Run main() with a recording wrapper that captures the resolved TokenStore.

    Drives the production ``token_store_factory`` slot via the
    ConnectDeps default — but wraps it so the test can read back the
    actual concrete store class chosen. F1-clean: no monkeypatching of
    kairix modules; the deps dataclass IS the public injection seam.
    """
    cs = _make_client_secret(tmp_path / "cs.json")
    captured: dict[str, object] = {}
    # Use the production default token_store_factory by reading it off
    # a fresh ConnectDeps() — that's the documented public seam.
    default_deps = ConnectDeps()
    real_factory = default_deps.token_store_factory

    def recording_factory(spec: str) -> object:
        try:
            store = real_factory(spec)
        except ValueError as exc:
            captured["error"] = exc
            raise
        captured["store"] = store
        return store

    listener = FakeCallbackListener()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=lambda _cmd, _path, _port: _FakeFlow(
            tokens=CapturedTokens(
                refresh_token="rt",
                access_token="at",
                token_uri=GOOGLE_TOKEN_URI,
            ),
            client=ClientCredentials(client_id="cid", client_secret="csec"),
        ),
        token_store_factory=recording_factory,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    # For the file store we need a writable target — override via
    # explicit --store=file is the production shape.
    rc = main(
        ["google-gmail", "--client-secret-path", str(cs), "--store", store_arg],
        deps=deps,
    )
    return rc, captured.get("store")


def test_cli_store_factory_routes_file(tmp_path: Path) -> None:
    """``--store=file`` builds a FileTokenStore (production default factory)."""
    rc, store = _capture_store_from_main(tmp_path, "file")
    assert rc == 0
    assert isinstance(store, FileTokenStore)


def test_cli_store_factory_routes_stdout(tmp_path: Path) -> None:
    rc, store = _capture_store_from_main(tmp_path, "stdout")
    assert rc == 0
    assert isinstance(store, StdoutTokenStore)


def test_cli_store_factory_routes_azure_kv_short(tmp_path: Path) -> None:
    # The Azure store fails when it tries to call Azure SDK without real
    # credentials; we only verify the factory routes to the right class.
    rc, store = _capture_store_from_main(tmp_path, "azure-kv:my-vault")
    assert isinstance(store, AzureKeyVaultTokenStore)
    # rc may be non-zero because the SDK lazy-import path raises; the
    # store-class assertion above is what we're pinning.
    _ = rc


def test_cli_store_factory_routes_azure_kv_url(tmp_path: Path) -> None:
    rc, store = _capture_store_from_main(
        tmp_path,
        "azure-kv:https://my-vault.vault.azure.net/",
    )
    assert isinstance(store, AzureKeyVaultTokenStore)
    _ = rc


# Bare ``--store=azure-kv`` (no suffix) reads ``$KAIRIX_KV_NAME`` —
# covered by tests/unit/test_connect_store_azure_kv.py::test_env_var_used_when_no_explicit
# via the constructor's ``env=`` injection seam (F2-clean). The
# CLI-side routing for that variant is exercised in
# test_cli_store_factory_routes_azure_kv_short above.


def test_cli_store_factory_unknown_returns_error_code(tmp_path: Path) -> None:
    """Unknown ``--store=`` value exits non-zero with F21 error."""
    stderr = io.StringIO()
    cs = _make_client_secret(tmp_path / "cs.json")
    deps = _ok_deps(stderr=stderr)
    # Override token_store_factory with a non-failing default and add
    # the bad spec via argv to trip the default factory's error path.
    deps_real = ConnectDeps(
        listener_factory=deps.listener_factory,
        oauth2_flow_factory=deps.oauth2_flow_factory,
        # use production default so the bad spec hits the real ValueError
        stdout=deps.stdout,
        stderr=stderr,
    )
    rc = main(
        ["google-gmail", "--client-secret-path", str(cs), "--store", "dropbox-secrets"],
        deps=deps_real,
    )
    assert rc == 1
    assert "unknown --store" in stderr.getvalue()


def test_cli_build_azure_kv_malformed_form_returns_error_code(tmp_path: Path) -> None:
    """``azure-kvgarbage`` (no colon) returns non-zero with F21 error via the CLI."""
    stderr = io.StringIO()
    cs = _make_client_secret(tmp_path / "cs.json")
    deps_real = ConnectDeps(
        listener_factory=lambda _h, _p: FakeCallbackListener(),
        oauth2_flow_factory=lambda _cmd, _path, _port: _FakeFlow(
            tokens=CapturedTokens(refresh_token="rt", access_token="at", token_uri=GOOGLE_TOKEN_URI),
            client=ClientCredentials(client_id="cid", client_secret="csec"),
        ),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(
        ["google-gmail", "--client-secret-path", str(cs), "--store", "azure-kvbroken"],
        deps=deps_real,
    )
    assert rc == 1
    assert "malformed --store" in stderr.getvalue()


def test_cli_token_store_failure_returns_nonzero(tmp_path: Path) -> None:
    cs = _make_client_secret(tmp_path / "cs.json")
    failing_store = FakeTokenStore(raises=TokenStoreUnauthorizedError("kv denied"))
    stderr = io.StringIO()
    deps = _ok_deps(store=failing_store, stderr=stderr)
    rc = main(["google-gmail", "--client-secret-path", str(cs)], deps=deps)
    assert rc == 1
    assert "kv denied" in stderr.getvalue()


def test_cli_listener_factory_oserror_returns_nonzero() -> None:
    stderr = io.StringIO()

    def boom(_host: str, _port: int) -> Any:
        raise OSError(
            "kairix connect: no free port. "
            "fix: stop port-blocker. next: lsof. run: kairix connect --port 9090 --client-secret-path <p>",
        )

    deps = ConnectDeps(
        listener_factory=boom,
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(["google-gmail", "--client-secret-path", "/tmp/x"], deps=deps)
    assert rc == 1
    assert "no free port" in stderr.getvalue()


def test_cli_subcommand_options_routing(tmp_path: Path) -> None:
    """Each Google subcommand routes to the matching ``service_area``."""
    cs = _make_client_secret(tmp_path / "cs.json")
    for cmd, area in (
        ("google-gmail", "gmail"),
        ("google-drive", "google-drive"),
        ("google-calendar", "google-calendar"),
    ):
        store = FakeTokenStore()
        deps = _ok_deps(store=store)
        rc = main([cmd, "--client-secret-path", str(cs)], deps=deps)
        assert rc == 0
        assert store.writes[0]["area"] == area


def test_cli_main_default_deps_does_not_raise_on_help(capsys: Any) -> None:
    """Calling main(['--help']) prints help and exits via argparse SystemExit."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    # argparse exits 0 for --help.
    assert exc_info.value.code == 0
