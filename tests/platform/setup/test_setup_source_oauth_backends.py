"""Unit tests for KairixSetupService's source-OAuth methods (#489).

Every test constructs the real service through ``build_setup_service``
with fakes injected at the ``SetupServiceDeps`` seams (F1/F2-clean —
no monkey-patching): ``FakeOAuth2Flow`` from ``tests/fakes.py`` plays
the provider flow, recorders capture secret persistence + config
writes, and the REAL ``WizardCallbackListener`` event dance runs so
the deliver/verify path is exercised end-to-end in-process.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from kairix.connect.protocols import CapturedTokens
from kairix.platform.setup.backends import (
    PHASE_CONSENT,
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_IDLE,
    SetupServiceDeps,
    read_config_mapping,
)
from kairix.platform.setup.service import SourceUnit, build_setup_service
from tests.fakes import FakeOAuth2Flow

pytestmark = pytest.mark.unit

_ORIGIN = "http://localhost:8080"
_SLACK_FIELDS = {"workspace": "alpha", "client_id": "id-1", "client_secret": "sec-1"}  # pragma: allowlist secret
_DEADLINE_S = 10.0


class _Harness:
    """One service wired with recorders + a scripted flow factory."""

    def __init__(self, *, flow: Any = None, discover: Any = None, write_config: Any = None) -> None:
        self.persisted_names: list[str] = []
        self.persisted_values: list[str] = []
        self.config_writes: list[dict[str, Any]] = []
        self.flow_requests: list[Any] = []
        self.flow = flow

        def flow_factory(request: Any) -> Any:
            self.flow_requests.append(request)
            resolved = self.flow if self.flow is not None else FakeOAuth2Flow(browser=request.browser)
            if getattr(resolved, "_browser", None) is None:
                resolved._browser = request.browser
            return resolved

        def persist(name: str, value: str) -> None:
            self.persisted_names.append(name)
            self.persisted_values.append(value)

        def record_write(updates: Any) -> Path:
            self.config_writes.append(dict(updates))
            return Path("recorded-config.yaml")

        self.service = build_setup_service(
            deps=SetupServiceDeps(
                oauth_flow_factory=flow_factory,
                persist_secret_fn=persist,
                discover_units_fn=discover if discover is not None else (lambda p, c, t: ()),
                read_config_fn=lambda: {},
                write_config_fn=write_config if write_config is not None else record_write,
            )
        )

    def wait_for_phase(self, *phases: str) -> Any:
        """Poll the public status surface until a terminal phase lands."""
        deadline = time.monotonic() + _DEADLINE_S
        while time.monotonic() < deadline:
            status = self.service.source_auth_status()
            if status.phase in phases:
                return status
            time.sleep(0.01)
        raise AssertionError(f"source auth never reached {phases}; last={self.service.source_auth_status()}")


def _start_slack(harness: _Harness) -> Any:
    started = harness.service.start_source_auth("slack", _SLACK_FIELDS, _ORIGIN)
    assert started.ok, started.error
    return harness.wait_for_phase(PHASE_CONSENT)


def _nonce_from(harness: _Harness) -> str:
    """The single-use state nonce, recovered from the flow request the
    factory saw (the wizard puts it in the authorize URL)."""
    return str(harness.flow_requests[-1].nonce)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_idle_before_any_start() -> None:
    harness = _Harness()
    status = harness.service.source_auth_status()
    assert status.phase == PHASE_IDLE
    assert status.authorize_url is None


def test_happy_path_consent_callback_done_and_canonical_secret_names() -> None:
    harness = _Harness()
    status = _start_slack(harness)
    assert status.provider == "slack"
    assert status.authorize_url == "https://provider.test/consent"
    nonce = _nonce_from(harness)
    outcome = harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    assert outcome.ok, outcome.error
    done = harness.wait_for_phase(PHASE_DONE, PHASE_FAILED)
    assert done.phase == PHASE_DONE, done.error
    # Canonical names asserted; values never asserted (F15 discipline).
    assert harness.persisted_names == [
        "kairix-connector-slack-alpha-client-id",
        "kairix-connector-slack-alpha-client-secret",
        "kairix-connector-slack-alpha-bot-token",
    ]


def test_flow_sees_the_origin_derived_redirect_uri() -> None:
    flow = FakeOAuth2Flow()
    harness = _Harness(flow=flow)
    _start_slack(harness)
    nonce = _nonce_from(harness)
    harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    harness.wait_for_phase(PHASE_DONE, PHASE_FAILED)
    assert flow.redirect_uris == [f"{_ORIGIN}/setup/oauth/callback"]


# ---------------------------------------------------------------------------
# Callback rejection — the guard-exemption's compensating control
# ---------------------------------------------------------------------------


def test_callback_with_no_pending_flow_is_rejected() -> None:
    harness = _Harness()
    outcome = harness.service.complete_source_callback("any", {"code": "auth-1"})
    assert not outcome.ok
    assert "No source connection is waiting" in (outcome.error or "")
    assert "fix:" in (outcome.error or "")


def test_callback_with_mismatched_state_is_rejected_and_slot_survives() -> None:
    harness = _Harness()
    _start_slack(harness)
    nonce = _nonce_from(harness)
    rejected = harness.service.complete_source_callback("forged-nonce", {"code": "evil", "state": "forged-nonce"})
    assert not rejected.ok
    assert "does not match" in (rejected.error or "")
    # The legitimate callback still works — a forged attempt must not
    # consume the operator's pending flow.
    accepted = harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    assert accepted.ok
    assert harness.wait_for_phase(PHASE_DONE, PHASE_FAILED).phase == PHASE_DONE


def test_callback_slot_is_single_use() -> None:
    harness = _Harness()
    _start_slack(harness)
    nonce = _nonce_from(harness)
    assert harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce}).ok
    replay = harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    assert not replay.ok
    assert "No source connection is waiting" in (replay.error or "")


def test_github_flow_accepts_stateless_callback() -> None:
    """The GitHub App install redirect carries no state param — the
    single-slot registry is the correlation."""
    pem = "-----BEGIN RSA PRIVATE KEY-----\nfakekeybody\n-----END RSA PRIVATE KEY-----\n"
    tokens = CapturedTokens(
        refresh_token="",
        access_token="ghs_fake",
        token_uri="https://github.test/token",
        metadata={"installation-id": "777"},
    )
    harness = _Harness(flow=FakeOAuth2Flow(service_area="github", tokens=tokens))
    started = harness.service.start_source_auth("github", {"app_id": "99", "private_key_pem": pem}, _ORIGIN)
    assert started.ok, started.error
    harness.wait_for_phase(PHASE_CONSENT)
    outcome = harness.service.complete_source_callback(None, {"installation_id": "777", "setup_action": "install"})
    assert outcome.ok, outcome.error
    assert harness.wait_for_phase(PHASE_DONE, PHASE_FAILED).phase == PHASE_DONE
    # Same derivation as `kairix connect github-app` (leaf_pairs +
    # SERVICE_LEAF_OVERRIDES): the App triple the connector resolver
    # reads, plus the ephemeral installation access token.
    assert harness.persisted_names == [
        "kairix-connector-github-app-id",
        "kairix-connector-github-app-private-key",
        "kairix-connector-github-access-token",
        "kairix-connector-github-installation-id",
    ]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_denied_consent_reports_guidance_not_a_traceback() -> None:
    harness = _Harness()
    _start_slack(harness)
    nonce = _nonce_from(harness)
    # The operator clicked Cancel: the provider redirects with error=.
    outcome = harness.service.complete_source_callback(nonce, {"error": "access_denied", "state": nonce})
    assert outcome.ok  # delivery succeeds; the FLOW records the denial
    failed = harness.wait_for_phase(PHASE_DONE, PHASE_FAILED)
    assert failed.phase == PHASE_FAILED
    assert "cancelled" in (failed.error or "")
    assert "fix:" in (failed.error or "")
    assert harness.persisted_names == []


def test_flow_exception_lands_as_f21_error() -> None:
    harness = _Harness(flow=FakeOAuth2Flow(raises=RuntimeError("token endpoint unreachable"), wait_for_listener=False))
    started = harness.service.start_source_auth("slack", _SLACK_FIELDS, _ORIGIN)
    assert started.ok
    failed = harness.wait_for_phase(PHASE_FAILED, PHASE_DONE)
    assert failed.phase == PHASE_FAILED
    assert "token endpoint unreachable" in (failed.error or "")
    assert "fix:" in (failed.error or "")


def test_unknown_provider_is_rejected_before_any_thread() -> None:
    harness = _Harness()
    started = harness.service.start_source_auth("carrier-pigeon", {}, _ORIGIN)
    assert not started.ok
    assert "fix:" in (started.error or "")
    assert harness.service.source_auth_status().phase == PHASE_IDLE


def test_flow_constructor_error_surfaces_on_the_form() -> None:
    """With the PRODUCTION flow factory, a missing workspace fails at
    construction and the message reaches the form — no thread starts."""
    from kairix.platform.setup.source_oauth import build_source_flow

    service = build_setup_service(
        deps=SetupServiceDeps(
            oauth_flow_factory=build_source_flow,
            persist_secret_fn=lambda _name, _value: None,
            discover_units_fn=lambda _p, _c, _t: (),
            read_config_fn=lambda: {},
            write_config_fn=lambda _updates: Path("recorded-config.yaml"),
        )
    )
    started = service.start_source_auth("slack", {"workspace": ""}, _ORIGIN)
    assert not started.ok
    assert "workspace" in (started.error or "")
    assert service.source_auth_status().phase == PHASE_IDLE


def test_second_start_replaces_the_pending_slot() -> None:
    harness = _Harness()
    _start_slack(harness)
    stale_nonce = _nonce_from(harness)
    started = harness.service.start_source_auth("slack", _SLACK_FIELDS, _ORIGIN)
    assert started.ok
    harness.wait_for_phase(PHASE_CONSENT)
    fresh_nonce = _nonce_from(harness)
    assert fresh_nonce != stale_nonce
    # The stale nonce no longer matches; the fresh one completes.
    assert not harness.service.complete_source_callback(stale_nonce, {"code": "x", "state": stale_nonce}).ok
    assert harness.service.complete_source_callback(fresh_nonce, {"code": "y", "state": fresh_nonce}).ok
    assert harness.wait_for_phase(PHASE_DONE, PHASE_FAILED).phase == PHASE_DONE


# ---------------------------------------------------------------------------
# Discovery + save
# ---------------------------------------------------------------------------


def _connect_slack(harness: _Harness) -> None:
    _start_slack(harness)
    nonce = _nonce_from(harness)
    harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    assert harness.wait_for_phase(PHASE_DONE, PHASE_FAILED).phase == PHASE_DONE


def test_discovery_requires_a_connected_source() -> None:
    harness = _Harness()
    units = harness.service.discover_source_units("slack")
    assert units.error is not None
    assert "not connected" in units.error


def test_discovery_returns_units_from_the_seam() -> None:
    rows = (SourceUnit(unit_id="C1", name="#general"), SourceUnit(unit_id="C2", name="#eng"))
    harness = _Harness(discover=lambda p, c, t: rows)
    _connect_slack(harness)
    units = harness.service.discover_source_units("slack")
    assert units.pickable
    assert units.units == rows
    assert units.error is None


def test_discovery_failure_renders_guidance() -> None:
    def boom(_p: str, _c: Any, _t: Any) -> tuple[SourceUnit, ...]:
        raise RuntimeError("429 from provider")

    harness = _Harness(discover=boom)
    _connect_slack(harness)
    units = harness.service.discover_source_units("slack")
    assert units.error is not None
    assert "429 from provider" in units.error
    assert "fix:" in units.error


def test_save_requires_a_connected_source() -> None:
    harness = _Harness()
    saved = harness.service.save_oauth_source("slack", "alpha", ("C1",))
    assert not saved.ok
    assert "not connected" in (saved.error or "")
    assert harness.config_writes == []


def test_save_rejects_empty_picks_for_pickable_sources() -> None:
    harness = _Harness()
    _connect_slack(harness)
    saved = harness.service.save_oauth_source("slack", "alpha", ())
    assert not saved.ok
    assert "tick at least one" in (saved.error or "")
    assert harness.config_writes == []


def test_save_emits_topology_config_and_pre_spend_summary() -> None:
    harness = _Harness()
    _connect_slack(harness)
    saved = harness.service.save_oauth_source("slack", "", ("C1", "C2"))
    assert saved.ok, saved.error
    assert saved.summary.startswith("2 channels selected")
    topology = harness.config_writes[0]["topology_v2"]
    assert topology["connectors"][0]["kind"] == "slack"
    # The workspace instance came from the connect form, not the save form.
    assert topology["connectors"][0]["connector_specific_config"] == {"workspace": "alpha"}
    filters = [s["path_filter"] for s in topology["collections"][0]["sources"]]
    assert filters == ["slack://channel/C1/*", "slack://channel/C2/*"]
    # #492 — the saved screen names the file the topology landed in.
    assert saved.config_file == "recorded-config.yaml"


def test_save_propagates_read_only_config_oserror() -> None:
    def read_only(_updates: Any) -> Path:
        raise OSError(30, "Read-only file system", "/etc/kairix/kairix.config.yaml")

    harness = _Harness(write_config=read_only)
    _connect_slack(harness)
    with pytest.raises(OSError, match="Read-only"):
        harness.service.save_oauth_source("slack", "", ("C1",))


def test_gmail_confirm_screen_requires_the_mailbox() -> None:
    google_tokens = CapturedTokens(
        refresh_token="fake-refresh",
        access_token="fake-access",
        token_uri="https://google.test/token",
    )
    pasted = '{"installed": {"client_id": "gid", "client_secret": "gsec"}}'  # pragma: allowlist secret
    harness = _Harness(flow=FakeOAuth2Flow(service_area="gmail", tokens=google_tokens))
    started = harness.service.start_source_auth("gmail", {"client_secret_json": pasted}, _ORIGIN)
    assert started.ok, started.error
    harness.wait_for_phase(PHASE_CONSENT)
    nonce = _nonce_from(harness)
    harness.service.complete_source_callback(nonce, {"code": "auth-1", "state": nonce})
    assert harness.wait_for_phase(PHASE_DONE, PHASE_FAILED).phase == PHASE_DONE
    units = harness.service.discover_source_units("gmail")
    assert not units.pickable
    assert "mailbox" in units.note
    missing = harness.service.save_oauth_source("gmail", "", ())
    assert not missing.ok
    assert "mailbox address is required" in (missing.error or "")
    saved = harness.service.save_oauth_source("gmail", "agent-alpha@example.com", ())
    assert saved.ok, saved.error
    connector = harness.config_writes[0]["topology_v2"]["connectors"][0]
    assert connector["connector_specific_config"] == {"user_email": "agent-alpha@example.com"}


# ---------------------------------------------------------------------------
# read_config_mapping (the save path's read side)
# ---------------------------------------------------------------------------


def test_read_config_mapping_prefers_the_overlay(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("topology_v2:\n  connectors: []\n", encoding="utf-8")
    loaded = read_config_mapping(overlay_path=str(overlay), config_path=str(tmp_path / "base.yaml"))
    assert loaded == {"topology_v2": {"connectors": []}}


def test_read_config_mapping_missing_file_reads_empty(tmp_path: Path) -> None:
    assert read_config_mapping(overlay_path=None, config_path=str(tmp_path / "absent.yaml")) == {}


def test_read_config_mapping_non_mapping_reads_empty(tmp_path: Path) -> None:
    weird = tmp_path / "weird.yaml"
    weird.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert read_config_mapping(overlay_path=str(weird), config_path=None) == {}
