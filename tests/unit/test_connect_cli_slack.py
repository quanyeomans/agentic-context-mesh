"""Unit-level coverage for the ``slack`` subcommand of kairix.connect.cli.

Mirrors ``tests/unit/test_connect_cli.py`` but pins the Slack-specific
argv shape (``--workspace``, ``--client-id``, ``--client-secret``) and
the per-workspace instance-slot routing.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from kairix.connect.cli import SUBCOMMAND_REGISTRY, ConnectDeps, main
from kairix.connect.oauth2.slack import SLACK_TOKEN_URI, SlackOAuth2Flow
from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from tests.fakes import (
    FakeBrowserLauncher,
    FakeCallbackListener,
    FakeTokenStore,
)

pytestmark = pytest.mark.unit


def _slack_flow_factory(browser: FakeBrowserLauncher) -> Any:
    """Build a SlackOAuth2Flow with a recording exchanger injected."""

    def factory(args: Any) -> SlackOAuth2Flow:
        def fake_exchanger(_c: ClientCredentials, code: str, _ru: str) -> CapturedTokens:
            return CapturedTokens(
                refresh_token="",
                access_token="",
                token_uri=SLACK_TOKEN_URI,
                bot_token=f"xoxb-{code}",
            )

        return SlackOAuth2Flow(
            workspace=args.workspace,
            client_id=args.client_id,
            client_secret=args.client_secret,
            browser=browser,
            token_exchanger=fake_exchanger,
        )

    return factory


def _slack_argv(workspace: str = "alpha", *, store: str = "file") -> list[str]:
    return [
        "slack",
        "--workspace",
        workspace,
        "--client-id",
        "cid-test",
        "--client-secret",
        "csec-test",  # pragma: allowlist secret
        "--store",
        store,
    ]


def test_subcommand_registry_includes_slack() -> None:
    """SUBCOMMAND_REGISTRY exposes 'slack' as a routed subcommand."""
    assert "slack" in SUBCOMMAND_REGISTRY
    assert SUBCOMMAND_REGISTRY["slack"].service_area == "slack"


def test_slack_happy_path_writes_with_workspace_instance() -> None:
    """End-to-end Slack success — store recorded with instance=workspace."""
    stdout = io.StringIO()
    stderr = io.StringIO()
    store = FakeTokenStore()
    listener = FakeCallbackListener()
    browser = FakeBrowserLauncher()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=_slack_flow_factory(browser),
        token_store_factory=lambda _spec: store,
        stdout=stdout,
        stderr=stderr,
    )
    rc = main(_slack_argv("alpha"), deps=deps)
    assert rc == 0, f"expected 0, got {rc}; stderr={stderr.getvalue()!r}"
    assert len(store.writes) == 1
    write = store.writes[0]
    assert write["area"] == "slack"
    assert write["instance"] == "alpha"  # per-workspace instance routing
    # The success summary carries the per-workspace canonical env-var name.
    out = stdout.getvalue()
    assert "KAIRIX_CONNECTOR_SLACK_ALPHA_BOT_TOKEN" in out, (
        f"expected per-workspace bot-token env-var in stdout, got: {out!r}"
    )


def test_slack_two_workspaces_route_to_distinct_instances() -> None:
    """Same Slack flow, two workspaces → two distinct instance writes."""
    browser = FakeBrowserLauncher()

    def factory(args: Any) -> SlackOAuth2Flow:
        return SlackOAuth2Flow(
            workspace=args.workspace,
            client_id=args.client_id,
            client_secret=args.client_secret,
            browser=browser,
            token_exchanger=lambda _c, _code, _ru: CapturedTokens(
                refresh_token="",
                access_token="",
                token_uri=SLACK_TOKEN_URI,
                bot_token=f"xoxb-{args.workspace}",
            ),
        )

    store = FakeTokenStore()
    listener = FakeCallbackListener()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: listener,
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: store,
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert main(_slack_argv("alpha"), deps=deps) == 0
    assert main(_slack_argv("coach"), deps=deps) == 0
    assert {w["instance"] for w in store.writes} == {"alpha", "coach"}, (
        f"expected two distinct workspace instances, got: {[w['instance'] for w in store.writes]!r}"
    )


def test_slack_store_failure_returns_nonzero() -> None:
    """A TokenStore failure for Slack surfaces exit 1 with the F21 hint on stderr."""
    stderr = io.StringIO()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: FakeCallbackListener(),
        oauth2_flow_factory=_slack_flow_factory(FakeBrowserLauncher()),
        token_store_factory=lambda _spec: FakeTokenStore(
            raises=TokenStoreUnauthorizedError("slack store rejected"),
        ),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    rc = main(_slack_argv(), deps=deps)
    assert rc == 1
    assert "slack store rejected" in stderr.getvalue()


def test_slack_subcommand_argparse_rejects_missing_workspace() -> None:
    """argparse exits 2 when ``--workspace`` is omitted from slack subcommand."""
    import contextlib

    captured_err = io.StringIO()
    with pytest.raises(SystemExit) as exc_info, contextlib.redirect_stderr(captured_err):
        main(["slack", "--client-id", "x", "--client-secret", "y"])
    assert exc_info.value.code == 2
    err = captured_err.getvalue()
    # argparse names the missing flag in its required-args error.
    assert "--workspace" in err or "workspace" in err
    assert "required" in err


def test_slack_summary_includes_team_when_set() -> None:
    """When the SlackOAuth2Flow exchange captured team_id/team_name, the summary names it."""
    browser = FakeBrowserLauncher()

    def factory(args: Any) -> SlackOAuth2Flow:
        flow = SlackOAuth2Flow(
            workspace=args.workspace,
            client_id=args.client_id,
            client_secret=args.client_secret,
            browser=browser,
            token_exchanger=lambda _c, _code, _ru: CapturedTokens(
                refresh_token="",
                access_token="",
                token_uri=SLACK_TOKEN_URI,
                bot_token="xoxb-y",
            ),
        )
        # Simulate the live-exchange branch having populated team metadata.
        flow.team_id = "T_VISIBLE"
        flow.team_name = "Visible Team"
        return flow

    stdout = io.StringIO()
    deps = ConnectDeps(
        listener_factory=lambda _h, _p: FakeCallbackListener(),
        oauth2_flow_factory=factory,
        token_store_factory=lambda _spec: FakeTokenStore(),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert main(_slack_argv("alpha"), deps=deps) == 0
    out = stdout.getvalue()
    assert "Visible Team" in out, f"expected team_name in summary, got: {out!r}"
    assert "T_VISIBLE" in out, f"expected team_id in summary, got: {out!r}"
