"""Step definitions for cli_connect_github_app.feature.

Drives ``kairix.connect.cli.main`` through ``ConnectDeps`` with fakes
from ``tests.fakes`` — F1-clean (no monkeypatching kairix internals),
F2-clean (no env-var mutation), F46-clean (composition through the
CLI entry point, not direct ``OAuth2Flow.authorize`` calls).
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.connect.cli import ConnectDeps
from kairix.connect.cli import main as connect_main
from kairix.connect.oauth2.github_app import GitHubAppFlow
from kairix.connect.protocols import CallbackResult, TokenStoreUnauthorizedError
from tests.fakes import (
    FakeBrowserLauncher,
    FakeCallbackListener,
    FakeTokenStore,
)

pytestmark = pytest.mark.bdd

scenarios("../features/cli_connect_github_app.feature")

# Minimal-but-realistic PEM body; the BDD path injects a JWT exchanger
# so we never actually sign with this key. Has the required BEGIN +
# PRIVATE KEY markers for the basic-shape validation.
_FAKE_PEM = (  # pragma: allowlist secret
    "-----BEGIN RSA PRIVATE KEY-----\nFAKE-PEM-BODY-FOR-BDD-TESTING-NOT-A-REAL-KEY\n-----END RSA PRIVATE KEY-----\n"
)


@pytest.fixture
def gh_app_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario state container for GitHub App connect BDD."""
    return {
        "tmp_path": tmp_path,
        "private_key_path": tmp_path / "app.pem",
        "listener": None,
        "browser": None,
        "store": None,
        "jwt_exchanger": None,
        "stdout": io.StringIO(),
        "stderr": io.StringIO(),
        "exit_code": None,
    }


@given("a GitHub App private key on disk")
def given_pem_present(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["private_key_path"].write_text(_FAKE_PEM)


@given("no GitHub App private key on disk")
def given_no_pem(gh_app_state: dict[str, Any]) -> None:
    # Path stays unwritten — discover_client_credentials will raise.
    pass


@given("a malformed GitHub App private key on disk")
def given_malformed_pem(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["private_key_path"].write_text("not a PEM file at all")


@given(parsers.parse('a fake callback listener that will return the install id "{install_id}"'))
def given_listener_with_install_id(gh_app_state: dict[str, Any], install_id: str) -> None:
    gh_app_state["listener"] = FakeCallbackListener(
        callback=CallbackResult(
            code="ignored",
            state=None,
            params={"installation_id": install_id, "setup_action": "install"},
        ),
    )


@given("a fake callback listener that will simulate a timeout")
def given_listener_timeout(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["listener"] = FakeCallbackListener(timeout=True)


@given("a fake browser that records every URL it is asked to open")
def given_browser(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["browser"] = FakeBrowserLauncher()


@given("a fake token store that records every store call")
def given_token_store(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["store"] = FakeTokenStore()


@given("a fake token store that raises on the next store call")
def given_token_store_raises(gh_app_state: dict[str, Any]) -> None:
    gh_app_state["store"] = FakeTokenStore(
        raises=TokenStoreUnauthorizedError(
            "fake store: backend write rejected. "
            "fix: confirm write permission. next: retry. run: kairix connect github-app --force",
        ),
    )


@given(parsers.parse('a fake JWT exchanger that returns the installation token "{token}"'))
def given_exchanger_returns_token(gh_app_state: dict[str, Any], token: str) -> None:
    def exchanger(_app_id: str, _pem: str, _install_id: str) -> str:
        return token

    gh_app_state["jwt_exchanger"] = exchanger


@given("a fake JWT exchanger that raises a signing failure")
def given_exchanger_raises_signing(gh_app_state: dict[str, Any]) -> None:
    def exchanger(_app_id: str, _pem: str, _install_id: str) -> str:
        raise RuntimeError(
            "kairix connect: GitHub App JWT signing failed: malformed key. "
            "fix: re-download the private key. next: re-run. run: kairix connect github-app",
        )

    gh_app_state["jwt_exchanger"] = exchanger


@given("a fake JWT exchanger that raises a token-exchange rejection")
def given_exchanger_raises_exchange(gh_app_state: dict[str, Any]) -> None:
    def exchanger(_app_id: str, _pem: str, _install_id: str) -> str:
        raise RuntimeError(
            "kairix connect: GitHub rejected installation-token exchange (401): Bad credentials. "
            "fix: confirm App id + installation_id. next: re-run. run: kairix connect github-app",
        )

    gh_app_state["jwt_exchanger"] = exchanger


@when(parsers.parse('the operator runs the github-app connect command with --app-id "{app_id}"'))
def when_operator_runs_github_app_connect(gh_app_state: dict[str, Any], app_id: str) -> None:
    listener = gh_app_state["listener"]
    browser = gh_app_state["browser"]
    store = gh_app_state["store"]
    exchanger = gh_app_state["jwt_exchanger"]

    def gh_app_flow_factory(args: argparse.Namespace) -> GitHubAppFlow:
        return GitHubAppFlow(
            app_id=args.app_id,
            private_key_path=args.private_key_path,
            app_slug=args.app_slug,
            browser=browser,
            token_exchanger=exchanger,
        )

    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=gh_app_flow_factory,
        token_store_factory=lambda _spec: store,
        stdout=gh_app_state["stdout"],
        stderr=gh_app_state["stderr"],
    )
    gh_app_state["exit_code"] = connect_main(
        [
            "github-app",
            "--app-id",
            app_id,
            "--private-key-path",
            str(gh_app_state["private_key_path"]),
        ],
        deps=deps,
    )


@then("the github-app command exits with status zero")
def then_gh_app_exits_zero(gh_app_state: dict[str, Any]) -> None:
    assert gh_app_state["exit_code"] == 0, (
        f"expected 0, got {gh_app_state['exit_code']}. stderr={gh_app_state['stderr'].getvalue()}"
    )


@then("the github-app command exits with a non-zero status")
def then_gh_app_exits_nonzero(gh_app_state: dict[str, Any]) -> None:
    assert gh_app_state["exit_code"] != 0


@then("the github-app token store recorded one write")
def then_gh_app_store_recorded_one(gh_app_state: dict[str, Any]) -> None:
    store: FakeTokenStore = gh_app_state["store"]
    assert len(store.writes) == 1, f"expected exactly one write, got {len(store.writes)}"


@then(parsers.parse('the github-app recorded area is "{area}"'))
def then_gh_app_recorded_area(gh_app_state: dict[str, Any], area: str) -> None:
    store: FakeTokenStore = gh_app_state["store"]
    assert store.writes[0]["area"] == area


@then("the github-app success summary names the canonical secret names")
def then_gh_app_summary_canonical(gh_app_state: dict[str, Any]) -> None:
    out = gh_app_state["stdout"].getvalue()
    # The GitHub App flow writes installation-id metadata + access-token only
    # (no client-id / client-secret / refresh-token base leaves with values).
    assert "KAIRIX_CONNECTOR_GITHUB_" in out, f"expected canonical prefix in stdout, got: {out!r}"
    assert "INSTALLATION_ID" in out, f"expected installation-id leaf, got: {out!r}"


@then("the github-app token metadata carries the installation id")
def then_gh_app_metadata_install_id(gh_app_state: dict[str, Any]) -> None:
    store: FakeTokenStore = gh_app_state["store"]
    tokens = store.writes[0]["tokens"]
    assert tokens.metadata.get("installation-id"), f"expected installation-id in metadata, got: {tokens.metadata!r}"


@then("the github-app error output points the operator at the GitHub App settings")
def then_gh_app_err_settings(gh_app_state: dict[str, Any]) -> None:
    err = gh_app_state["stderr"].getvalue()
    # Strengthened: must mention BOTH the PEM file (the unrecoverable) AND
    # the GitHub settings URL (the actionable next step) so a regression
    # dropping either half is caught.
    assert "private key" in err.lower(), f"expected 'private key' in stderr, got: {err!r}"
    assert "github.com/settings/apps" in err, f"expected 'github.com/settings/apps' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err, (
        f"expected F21 fix/next/run markers in stderr, got: {err!r}"
    )


@then("the github-app error output mentions the install callback wait")
def then_gh_app_err_callback(gh_app_state: dict[str, Any]) -> None:
    err = gh_app_state["stderr"].getvalue().lower()
    assert "callback" in err, f"expected 'callback' in stderr, got: {err!r}"
    assert "timeout" in err or "within" in err, f"expected time-out indication in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err


@then("the github-app error output mentions the private key format")
def then_gh_app_err_pem_format(gh_app_state: dict[str, Any]) -> None:
    err = gh_app_state["stderr"].getvalue()
    assert "PEM" in err or "private key" in err.lower(), f"expected PEM/private-key in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err


@then("the github-app error output mentions JWT signing")
def then_gh_app_err_jwt_signing(gh_app_state: dict[str, Any]) -> None:
    err = gh_app_state["stderr"].getvalue()
    # Strengthened: must mention BOTH "JWT" AND "signing" so a regression
    # that drops the rationale is caught.
    assert "JWT" in err, f"expected 'JWT' in stderr, got: {err!r}"
    assert "signing" in err.lower(), f"expected 'signing' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err


@then("the github-app error output mentions the installation-token exchange")
def then_gh_app_err_exchange(gh_app_state: dict[str, Any]) -> None:
    err = gh_app_state["stderr"].getvalue()
    assert "installation-token exchange" in err, f"expected 'installation-token exchange' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err
