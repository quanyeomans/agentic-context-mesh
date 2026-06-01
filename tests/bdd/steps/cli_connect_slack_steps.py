"""Step definitions for cli_connect_slack.feature.

Drives ``kairix.connect.cli.main`` through ``ConnectDeps`` with fakes
from ``tests.fakes`` — F1-clean (no monkeypatching kairix internals),
F2-clean (no env-var mutation), F46-clean (composition through the
CLI entry point, not direct ``SlackOAuth2Flow.authorize`` calls).

Mirrors the Google step-impls shape from
``tests/bdd/steps/cli_connect_google_steps.py``; per-service step
names are prefixed with ``slack`` so the two features' step grammars
don't collide.
"""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from kairix.connect.cli import ConnectDeps
from kairix.connect.cli import main as connect_main
from kairix.connect.oauth2.slack import (
    SLACK_TOKEN_URI,
    SlackOAuth2Flow,
)
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

scenarios("../features/cli_connect_slack.feature")


@pytest.fixture
def slack_state() -> dict[str, Any]:
    """Per-scenario state container — fakes + captured stdout/stderr/exit."""
    return {
        "client_id": "",
        "client_secret": "",
        "listener": None,
        "listener_raises": None,
        "browser": None,
        "store": None,
        "stdout": io.StringIO(),
        "stderr": io.StringIO(),
        "exit_code": None,
    }


@given("a Slack OAuth client_id and client_secret")
def given_slack_credentials(slack_state: dict[str, Any]) -> None:
    slack_state["client_id"] = "bdd-slack-cid"
    slack_state["client_secret"] = "bdd-slack-csec"  # pragma: allowlist secret


@given("no Slack client credentials supplied")
def given_no_slack_credentials(slack_state: dict[str, Any]) -> None:
    slack_state["client_id"] = ""
    slack_state["client_secret"] = ""


@given(parsers.parse('a fake callback listener that will return the code "{code}"'))
def given_slack_listener_with_code(slack_state: dict[str, Any], code: str) -> None:
    slack_state["listener"] = FakeCallbackListener(
        callback=CallbackResult(code=code, state=None),
    )


@given("a fake callback listener that will simulate a denied Slack consent")
def given_slack_listener_denied(slack_state: dict[str, Any]) -> None:
    slack_state["listener"] = FakeCallbackListener(denied=True)


@given("a fake callback listener that will simulate a Slack timeout")
def given_slack_listener_timeout(slack_state: dict[str, Any]) -> None:
    slack_state["listener"] = FakeCallbackListener(timeout=True)


@given("a listener factory that raises a port-in-use OSError")
def given_slack_port_collision(slack_state: dict[str, Any]) -> None:
    slack_state["listener_raises"] = OSError(
        "kairix connect: port 8080 in use. "
        "fix: stop the service bound to port 8080 OR pass --port to a free one. "
        "next: lsof -nP -iTCP:8080 -sTCP:LISTEN. "
        "run: kairix connect slack --workspace <name> --port 9090 --client-id <id> --client-secret <s>",
    )


@given("a fake browser that records every URL it is asked to open")
def given_slack_browser(slack_state: dict[str, Any]) -> None:
    slack_state["browser"] = FakeBrowserLauncher()


@given("a fake token store that records every Slack store call")
def given_slack_token_store(slack_state: dict[str, Any]) -> None:
    slack_state["store"] = FakeTokenStore()


@given("a fake token store that raises on the next Slack store call")
def given_slack_token_store_raises(slack_state: dict[str, Any]) -> None:
    slack_state["store"] = FakeTokenStore(
        raises=TokenStoreUnauthorizedError(
            "fake store: slack backend write rejected. "
            "fix: confirm write permission on the slack backend. "
            "next: retry with --force. run: kairix connect slack --workspace alpha --force",
        ),
    )


def _slack_flow_factory(
    state: dict[str, Any],
) -> Any:
    """Build a SlackOAuth2Flow with a recording exchanger injected."""

    def factory(args: argparse.Namespace) -> SlackOAuth2Flow:
        def fake_exchanger(_client: ClientCredentials, code: str, _ru: str) -> CapturedTokens:
            return CapturedTokens(
                refresh_token="",  # Slack returns no refresh_token — documented partial.
                access_token="",
                token_uri=SLACK_TOKEN_URI,
                bot_token=f"xoxb-bdd-{code}",
            )

        return SlackOAuth2Flow(
            workspace=args.workspace,
            client_id=args.client_id,
            client_secret=args.client_secret,
            browser=state["browser"],
            token_exchanger=fake_exchanger,
        )

    return factory


def _build_listener_factory(state: dict[str, Any]) -> Any:
    """Return a factory that either yields the recorded listener or raises."""
    raises = state.get("listener_raises")
    listener = state["listener"]

    def factory(_h: str, _p: int) -> Any:
        if raises is not None:
            raise raises
        return listener

    return factory


@when(parsers.parse('the operator runs the slack connect command for workspace "{workspace}"'))
def when_operator_runs_slack_connect(slack_state: dict[str, Any], workspace: str) -> None:
    deps = ConnectDeps(
        listener_factory=_build_listener_factory(slack_state),
        oauth2_flow_factory=_slack_flow_factory(slack_state),
        token_store_factory=lambda _spec: slack_state["store"],
        stdout=slack_state["stdout"],
        stderr=slack_state["stderr"],
    )
    slack_state["exit_code"] = connect_main(
        [
            "slack",
            "--workspace",
            workspace,
            "--client-id",
            slack_state["client_id"],
            "--client-secret",
            slack_state["client_secret"],
        ],
        deps=deps,
    )


@when("the operator runs the slack connect command missing the client_id")
def when_operator_runs_slack_missing_client_id(slack_state: dict[str, Any]) -> None:
    """argparse rejects the call before any ConnectDeps factory runs.

    argparse writes its own help/error to ``sys.stderr`` directly (not
    through the deps' captured stderr), so we redirect ``sys.stderr``
    for the duration of the call so the BDD assertion can read it.
    F1-clean: ``contextlib.redirect_stderr`` is a stdlib redirect, not
    a kairix-module monkeypatch.
    """
    import contextlib
    import sys

    deps = ConnectDeps(
        listener_factory=lambda _h, _p: FakeCallbackListener(),
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=slack_state["stdout"],
        stderr=slack_state["stderr"],
    )
    captured = io.StringIO()
    try:
        with contextlib.redirect_stderr(captured):
            slack_state["exit_code"] = connect_main(
                [
                    "slack",
                    "--workspace",
                    "alpha",
                    # --client-id omitted on purpose
                    "--client-secret",
                    "csec",
                ],
                deps=deps,
            )
    except SystemExit as exc:
        # argparse calls sys.exit(2) on missing required args; capture
        # the exit code so the assertions can read it.
        slack_state["exit_code"] = int(exc.code) if exc.code is not None else 2
    finally:
        # Drain the captured argparse output into the state's stderr
        # so the existing assertions read it through one channel.
        slack_state["stderr"].write(captured.getvalue())
        # Ensure sys.stderr is restored even on weird exit paths.
        sys.stderr.flush()


@then("the slack connect command exits with status zero")
def then_slack_exits_zero(slack_state: dict[str, Any]) -> None:
    assert slack_state["exit_code"] == 0, (
        f"expected 0, got {slack_state['exit_code']}. stderr={slack_state['stderr'].getvalue()}"
    )


@then("the slack connect command exits with a non-zero status")
def then_slack_exits_nonzero(slack_state: dict[str, Any]) -> None:
    assert slack_state["exit_code"] != 0


@then("the slack token store recorded one write")
def then_slack_store_recorded_one(slack_state: dict[str, Any]) -> None:
    store: FakeTokenStore = slack_state["store"]
    assert len(store.writes) == 1


@then(parsers.parse('the slack recorded area is "{area}"'))
def then_slack_recorded_area(slack_state: dict[str, Any], area: str) -> None:
    store: FakeTokenStore = slack_state["store"]
    assert store.writes[0]["area"] == area


@then(parsers.parse('the slack recorded instance is "{instance}"'))
def then_slack_recorded_instance(slack_state: dict[str, Any], instance: str) -> None:
    """Per-workspace instance routing — pinned via the recorded kwargs."""
    store: FakeTokenStore = slack_state["store"]
    assert store.writes[0]["instance"] == instance, (
        f"expected instance={instance!r}, got {store.writes[0]['instance']!r}"
    )


@then("the slack success summary names the canonical bot token")
def then_slack_summary_names_bot_token(slack_state: dict[str, Any]) -> None:
    out = slack_state["stdout"].getvalue()
    # Must mention BOTH the canonical workspace-scoped env var prefix AND
    # the bot-token leaf, so a regression that drops either side is caught.
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA" in out, f"expected per-workspace env-var prefix in stdout, got: {out!r}"
    assert "BOT_TOKEN" in out, f"expected BOT_TOKEN leaf in stdout, got: {out!r}"


@then("the slack error output mentions consent")
def then_slack_err_mentions_consent(slack_state: dict[str, Any]) -> None:
    err = slack_state["stderr"].getvalue().lower()
    # Must mention BOTH the operator-visible noun and the F21 markers
    # so a regression that drops either is caught.
    assert "consent denied" in err, f"expected 'consent denied' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err, (
        f"expected F21 fix/next/run markers in stderr, got: {err!r}"
    )


@then("the slack error output mentions the callback wait")
def then_slack_err_mentions_callback(slack_state: dict[str, Any]) -> None:
    err = slack_state["stderr"].getvalue().lower()
    assert "callback" in err, f"expected 'callback' in stderr, got: {err!r}"
    assert "timeout" in err or "within" in err, (
        f"expected time-out indication ('timeout' or 'within ...s') in stderr, got: {err!r}"
    )
    assert "fix:" in err and "next:" in err and "run:" in err, (
        f"expected F21 fix/next/run markers in stderr, got: {err!r}"
    )


@then("the slack error output mentions the port")
def then_slack_err_mentions_port(slack_state: dict[str, Any]) -> None:
    err = slack_state["stderr"].getvalue().lower()
    assert "port" in err, f"expected 'port' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err, (
        f"expected F21 fix/next/run markers in stderr, got: {err!r}"
    )


@then("the slack argparse error mentions the missing client_id argument")
def then_slack_argparse_mentions_client_id(slack_state: dict[str, Any]) -> None:
    err = slack_state["stderr"].getvalue().lower()
    # argparse names the missing flag; the assertion pins BOTH the
    # actionable flag-name AND a 'required' hint so a regression that
    # silently makes the flag optional is caught.
    assert "--client-id" in err or "client_id" in err, f"expected 'client_id' in argparse stderr, got: {err!r}"
    assert "required" in err or "the following arguments are required" in err, (
        f"expected 'required' rationale in argparse stderr, got: {err!r}"
    )


@then("the slack error output mentions the store backend")
def then_slack_err_mentions_store(slack_state: dict[str, Any]) -> None:
    err = slack_state["stderr"].getvalue().lower()
    assert "store" in err, f"expected 'store' in stderr, got: {err!r}"
    assert "backend" in err, f"expected 'backend' in stderr, got: {err!r}"
    assert "fix:" in err and "next:" in err and "run:" in err, (
        f"expected F21 fix/next/run markers in stderr, got: {err!r}"
    )
