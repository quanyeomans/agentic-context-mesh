"""F54 both-branch coverage for the ``setup_wizard_web`` feature flag.

Composes the real ASGI app via
:func:`kairix.agents.mcp.transport.build_mcp_app` with the flag pinned
OFF and ON through ``FakeFeatureFlagResolver`` (the canonical F54
pattern). Asserts:

* **OFF (seam-injected)** — no ``/setup`` routes exist (404); the MCP
  transport + health surfaces are byte-for-byte the pre-flag shape.
* **ON** — the wizard serves the full journey surface; the MCP
  transport + health surfaces are unchanged alongside it.

The flag now defaults ON (cutover), so the OFF branch sets the flag
off explicitly through the seam rather than leaning on the default;
``test_registry_default_is_on_cutover_stage`` pins the cutover itself.

F1/F2-clean: flag state comes from the resolver fake through the
``setup_wizard_enabled`` seam — no env vars, no REGISTRY mutation.
"""

from __future__ import annotations

import pytest

# starlette ships via the optional [agents] extra (transitive dep of mcp);
# CI's base-deps stages must skip rather than fail on the missing import.
starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from kairix.agents.mcp.transport import build_mcp_app  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeFeatureFlagResolver,
    FakeMcpTransportServer,
    FakeSecretsLoader,
    FakeSetupService,
)

pytestmark = pytest.mark.integration

_FLAG = "setup_wizard_web"
_LOOPBACK = ("127.0.0.1", 9999)


def _compose(resolver: FakeFeatureFlagResolver, service: FakeSetupService) -> TestClient:
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: resolver.get(_FLAG),
    )
    return TestClient(app, client=_LOOPBACK)


def test_registry_default_is_on_cutover_stage() -> None:
    """Pin the cutover: the wizard ships reachable out-of-the-box.

    A default install (env-unset, config-unset) resolves the registry
    default, so default=True is what makes GET /setup=200 on a fresh
    Docker/pip boot. stage="cutover" records that this is a deliberate,
    validated default-flip — not the introduce-stage default-OFF the
    wizard landed under. Sabotage-proof for the flip: reverting either
    field turns this red.
    """
    from kairix.core.features.registry import REGISTRY

    entry = REGISTRY["setup_wizard_web"]
    assert entry.default is True
    assert entry.stage == "cutover"


def test_off_branch_mounts_no_setup_routes() -> None:
    # default-ON now, so the OFF branch must set the flag off explicitly
    # (don't lean on the registry default) — the seam injection keeps
    # F54's OFF-branch (404) coverage valid post-cutover.
    resolver = FakeFeatureFlagResolver().with_flag("setup_wizard_web", False)
    client = _compose(resolver, FakeSetupService())

    assert client.get("/setup", follow_redirects=True).status_code == 404
    assert client.get("/setup/provider").status_code == 404
    assert client.get("/setup/static/kairix.css").status_code == 404
    # Pre-flag surfaces are untouched.
    assert client.get("/mcp").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_on_branch_serves_the_wizard_journey() -> None:
    resolver = FakeFeatureFlagResolver().with_flag("setup_wizard_web", True)
    service = FakeSetupService()
    client = _compose(resolver, service)

    welcome = client.get("/setup", follow_redirects=True)
    assert welcome.status_code == 200
    assert "Welcome to kairix" in welcome.text

    # Fixture credential for the fake service, not a real key.
    payload = {"provider": "anthropic", "api_key": "fake-key-for-tests", "endpoint": ""}  # pragma: allowlist secret
    validation = client.post("/setup/key/validate", data=payload)
    assert "Key validated successfully" in validation.text

    scan = client.post("/setup/folder/scan", data={"folder_path": "~/Documents"})
    assert "Scan complete" in scan.text

    # The MCP transport + health surfaces serve unchanged alongside.
    assert client.get("/mcp").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_on_branch_index_progress_reaches_done() -> None:
    resolver = FakeFeatureFlagResolver().with_flag("setup_wizard_web", True)
    service = FakeSetupService(chunks_total=100, chunks_per_tick=50)
    client = _compose(resolver, service)

    start = client.post("/setup/folder", data={"folder_path": "~/Documents"}, follow_redirects=False)
    assert start.status_code == 303
    assert service.start_index_calls == 1

    first = client.get("/setup/indexing/progress")
    assert "HX-Redirect" not in first.headers
    second = client.get("/setup/indexing/progress")
    assert second.headers["HX-Redirect"] == "/setup/tour"
