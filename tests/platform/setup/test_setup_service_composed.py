"""Composed-path integration proof for the real SetupService (#474).

The real backend (``build_setup_service``) is mounted behind the REAL
wizard routes via the production transport composer
(``build_mcp_app``), with fakes injected only at the seams BELOW the
service — fake provider plugin, recorder persistence, tmp-path config
file, scripted index counters, canonical ``FakeSearchPipeline``. The
test then walks the operator's actual journey through the rendered
HTML (F46 composition: CLI/MCP/factory surfaces only, no direct
pipeline construction; F47 spirit: paths flow through ``FakePaths``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

# starlette ships via the optional [agents] extra (transitive dep of mcp);
# CI's base-deps stages must skip rather than fail on the missing import.
starlette = pytest.importorskip("starlette")
from starlette.testclient import TestClient  # noqa: E402

from kairix.agents.mcp.transport import build_mcp_app  # noqa: E402
from kairix.platform.setup.backends import SetupServiceDeps, update_config_file  # noqa: E402
from kairix.platform.setup.service import build_setup_service  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeMcpTransportServer,
    FakePaths,
    FakeProvider,
    FakeSearchPipeline,
    FakeSecretsLoader,
)

pytestmark = pytest.mark.integration

_LOOPBACK = ("127.0.0.1", 9999)
# Fixture credential for the fake provider seam, not a real key.
_FAKE_KEY = "fake-key-for-tests"  # pragma: allowlist secret


def test_real_service_drives_the_full_wizard_journey(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "kickoff.md").write_text("agent-alpha agreed the rollout starts next sprint. " * 20)
    (docs / "retro.md").write_text("the retro decided to keep the weekly demo cadence. " * 20)
    config_file = tmp_path / "kairix.config.yaml"
    paths = FakePaths(
        document_root=docs,
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )

    persisted: list[tuple[str, str, str]] = []
    index_runs: list[int] = []
    # Index counters: zero until the (injected) index run records itself,
    # then 6 embedded / 0 pending — so the progress poll observes done.
    counts = {"embedded": 0, "pending": 6}

    def persist(api_key: str, endpoint: str, model: str) -> Path | None:
        persisted.append((api_key, endpoint, model))
        return tmp_path / "secrets" / "kairix.env"

    def run_index() -> None:
        index_runs.append(1)
        counts["embedded"], counts["pending"] = 6, 0

    search_rows = [
        FakeSearchPipeline.make_chunk_row(
            path="docs/kickoff.md",
            title="Project kickoff notes",
            content="agent-alpha agreed the rollout starts next sprint",
        )
    ]
    deps = SetupServiceDeps(
        provider_factory=lambda name, creds: FakeProvider(name=name, vector=[0.1] * 8, dim=8),
        persist_credentials_fn=persist,
        credentials_probe=lambda: bool(persisted),
        configured_document_root_fn=lambda: docs,
        write_config_fn=lambda updates: update_config_file(config_file, updates),
        index_counts_fn=lambda db: (counts["embedded"], counts["pending"]),
        embed_lock_probe_fn=lambda lock: False,
        index_runner_fn=run_index,
        search_pipeline_factory=lambda p: FakeSearchPipeline(scripted_results=search_rows),
        capability_probe_fn=lambda: {
            "secrets_loaded": True,
            "vector_search_capable": True,
            "bm25_search_capable": True,
            "detail": {},
        },
        tools_count_fn=lambda: 12,
        environ={},
    )
    service = build_setup_service(paths=paths, deps=deps)
    app = build_mcp_app(
        FakeMcpTransportServer(),
        setup_service_factory=lambda: service,
        setup_secrets=FakeSecretsLoader(),
        setup_wizard_enabled=lambda: True,
    )
    client = TestClient(app, client=_LOOPBACK)

    # Welcome → provider picker (real plugin registry names).
    welcome = client.get("/setup", follow_redirects=True)
    assert welcome.status_code == 200
    assert "Welcome to kairix" in welcome.text

    # Validate the typed-in key against the (fake) provider plugin.
    validated = client.post(
        "/setup/key/validate",
        data={"provider": "openai", "api_key": _FAKE_KEY, "endpoint": ""},
    )
    assert validated.status_code == 200
    assert "Key validated successfully" in validated.text
    assert "text-embedding-3-large" in validated.text
    assert _FAKE_KEY not in validated.text  # F15 — key never echoed

    # Save the provider: credential persists, provider: lands in the config.
    saved = client.post(
        "/setup/key",
        data={"provider": "openai", "api_key": _FAKE_KEY, "endpoint": "", "model": "text-embedding-3-large"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert persisted == [(_FAKE_KEY, "https://api.openai.com/v1", "text-embedding-3-large")]
    assert yaml.safe_load(config_file.read_text())["provider"] == "openai"

    # Scan the REAL tmp folder — the service walks the filesystem itself.
    scan = client.post("/setup/folder/scan", data={"folder_path": str(docs)})
    assert scan.status_code == 200
    assert "Scan complete" in scan.text
    assert "<dd>2</dd>" in scan.text  # 2 markdown files found

    # Save the folder: document_root merges into the same config file and
    # the first index run kicks off in the background.
    folder_saved = client.post("/setup/folder", data={"folder_path": str(docs)}, follow_redirects=False)
    assert folder_saved.status_code == 303
    config = yaml.safe_load(config_file.read_text())
    assert config["provider"] == "openai"  # earlier key preserved by the merge
    assert config["paths"]["document_root"] == str(docs)

    # Poll progress until the background run lands; the done state redirects.
    import time

    done_response: Any = None
    for _ in range(500):
        progress = client.get("/setup/indexing/progress")
        if progress.headers.get("HX-Redirect") == "/setup/tour":
            done_response = progress
            break
        time.sleep(0.01)
    assert done_response is not None, "indexing never reported done"
    assert "Indexing complete" in done_response.text
    assert index_runs == [1]

    # First search renders the pipeline's hits as result cards.
    results = client.post("/setup/search", data={"query": "project kickoff"})
    assert results.status_code == 200
    assert "Project kickoff notes" in results.text
    assert "docs/kickoff.md" in results.text
    # FakeSearchPipeline's canonical rows carry no fusion score, so the
    # relative normalisation reads 0%; the score mapping itself is pinned
    # by the unit tests in test_setup_service.py.
    assert "% match" in results.text

    # Connect-agent screen surfaces the MCP URL + doc-shaped snippets.
    # The displayed URL re-anchors on the live request origin (review
    # L1) — the test client's origin here — keeping the endpoint path.
    connect = client.get("/setup/connect-agent")
    assert connect.status_code == 200
    assert "http://testserver/mcp" in connect.text
    assert "mcpServers" in connect.text

    # Handshake verification reports the in-process tool count.
    verify = client.post("/setup/connect-agent/verify")
    assert verify.status_code == 200
    assert "Agent connected" in verify.text
    assert "12 tools available" in verify.text

    # Done screen celebrates the indexed chunk count.
    done = client.get("/setup/done")
    assert done.status_code == 200
    assert "Your knowledge is ready" in done.text
    assert "chunks indexed" in done.text
