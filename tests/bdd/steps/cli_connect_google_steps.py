"""Step definitions for cli_connect_google.feature.

Drives ``kairix.connect.cli.main`` through ``ConnectDeps`` with fakes
from ``tests.fakes`` — F1-clean (no monkeypatching kairix internals),
F2-clean (no env-var mutation), F46-clean (composition through the
CLI entry point, not direct ``OAuth2Flow.authorize`` calls).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.connect.cli import ConnectDeps
from kairix.connect.cli import main as connect_main
from kairix.connect.oauth2.google import GOOGLE_TOKEN_URI, GoogleOAuth2Flow
from kairix.connect.protocols import (
    CallbackResult,
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from tests.fakes import (
    FakeBrowserLauncher,
    FakeCallbackListener,
    FakeTokenStore,
)

pytestmark = pytest.mark.bdd

scenarios("../features/cli_connect.feature")


@pytest.fixture
def connect_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario state container.

    Holds the fake listener / browser / store / client_secret path +
    the captured stdout / stderr buffers + the final exit code.
    """
    return {
        "tmp_path": tmp_path,
        "client_secret_path": tmp_path / "client_secret.json",
        "listener": None,
        "browser": None,
        "store": None,
        "stdout": io.StringIO(),
        "stderr": io.StringIO(),
        "exit_code": None,
    }


def _write_client_secret(path: Path) -> None:
    payload = {
        "installed": {
            "client_id": "bdd-cid",
            "client_secret": "bdd-csec",  # pragma: allowlist secret
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": GOOGLE_TOKEN_URI,
        },
    }
    path.write_text(json.dumps(payload))


@given("a Google client_secret.json downloaded to a temp path")
def given_client_secret_present(connect_state: dict[str, Any]) -> None:
    _write_client_secret(connect_state["client_secret_path"])


@given("no Google client_secret.json on disk")
def given_no_client_secret(connect_state: dict[str, Any]) -> None:
    # Path stays unwritten — discover_client_credentials will raise.
    pass


@given(parsers.parse('a fake callback listener that will return the code "{code}"'))
def given_listener_with_code(connect_state: dict[str, Any], code: str) -> None:
    connect_state["listener"] = FakeCallbackListener(
        callback=CallbackResult(code=code, state=None),
    )


@given("a fake callback listener that will simulate a denied consent")
def given_listener_denied(connect_state: dict[str, Any]) -> None:
    connect_state["listener"] = FakeCallbackListener(denied=True)


@given("a fake callback listener that will simulate a timeout")
def given_listener_timeout(connect_state: dict[str, Any]) -> None:
    connect_state["listener"] = FakeCallbackListener(timeout=True)


@given("a fake browser that records every URL it is asked to open")
def given_browser(connect_state: dict[str, Any]) -> None:
    connect_state["browser"] = FakeBrowserLauncher()


@given("a fake token store that records every store call")
def given_token_store(connect_state: dict[str, Any]) -> None:
    connect_state["store"] = FakeTokenStore()


@given("a fake token store that raises on the next store call")
def given_token_store_raises(connect_state: dict[str, Any]) -> None:
    connect_state["store"] = FakeTokenStore(
        raises=TokenStoreUnauthorizedError(
            "fake store: backend write rejected. "
            "fix: confirm write permission on the store backend. "
            "next: retry with --force. run: kairix connect google-gmail --force",
        ),
    )


@when(parsers.parse('the operator runs the connect command for "{subcommand}"'))
def when_operator_runs_connect(connect_state: dict[str, Any], subcommand: str) -> None:
    listener = connect_state["listener"]
    browser = connect_state["browser"]
    store = connect_state["store"]

    def flow_factory(cmd: str, path: Path, _port: int) -> GoogleOAuth2Flow:
        # Build a real GoogleOAuth2Flow but inject the fake browser +
        # token exchanger so the consent dance is fully driven by fakes.
        area = {
            "google-gmail": "gmail",
            "google-drive": "google-drive",
            "google-calendar": "google-calendar",
        }[cmd]

        def fake_exchanger(_client: ClientCredentials, code: str, _ru: str) -> CapturedTokens:
            return CapturedTokens(
                refresh_token=f"refresh-{code}",
                access_token=f"access-{code}",
                token_uri=GOOGLE_TOKEN_URI,
            )

        return GoogleOAuth2Flow(
            service_area=area,
            client_secret_path=path,
            browser=browser,
            token_exchanger=fake_exchanger,
        )

    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=flow_factory,
        token_store_factory=lambda _spec: store,
        stdout=connect_state["stdout"],
        stderr=connect_state["stderr"],
    )
    connect_state["exit_code"] = connect_main(
        [subcommand, "--client-secret-path", str(connect_state["client_secret_path"])],
        deps=deps,
    )


@then("the command exits with status zero")
def then_exits_zero(connect_state: dict[str, Any]) -> None:
    assert connect_state["exit_code"] == 0, (
        f"expected 0, got {connect_state['exit_code']}. stderr={connect_state['stderr'].getvalue()}"
    )


@then("the command exits with a non-zero status")
def then_exits_nonzero(connect_state: dict[str, Any]) -> None:
    assert connect_state["exit_code"] != 0


@then("the token store recorded one write")
def then_store_recorded_one(connect_state: dict[str, Any]) -> None:
    store: FakeTokenStore = connect_state["store"]
    assert len(store.writes) == 1


@then(parsers.parse('the recorded area is "{area}"'))
def then_recorded_area(connect_state: dict[str, Any], area: str) -> None:
    store: FakeTokenStore = connect_state["store"]
    assert store.writes[0]["area"] == area


@then("the success summary names the canonical secret names")
def then_summary_names_canonical(connect_state: dict[str, Any]) -> None:
    out = connect_state["stdout"].getvalue()
    assert "KAIRIX_CONNECTOR_" in out
    assert "CLIENT_ID" in out
    assert "REFRESH_TOKEN" in out


@then("the error output mentions consent")
def then_err_mentions_consent(connect_state: dict[str, Any]) -> None:
    err = connect_state["stderr"].getvalue().lower()
    assert "consent" in err or "denied" in err


@then("the error output mentions the listener wait")
def then_err_mentions_listener(connect_state: dict[str, Any]) -> None:
    err = connect_state["stderr"].getvalue().lower()
    assert "timeout" in err or "callback" in err or "listener" in err


@then("the error output points the operator at the GCP console")
def then_err_points_gcp(connect_state: dict[str, Any]) -> None:
    err = connect_state["stderr"].getvalue()
    assert "GCP console" in err or "client_secret.json" in err


@then("the error output mentions the store backend")
def then_err_mentions_store(connect_state: dict[str, Any]) -> None:
    err = connect_state["stderr"].getvalue().lower()
    assert "store" in err or "backend" in err
