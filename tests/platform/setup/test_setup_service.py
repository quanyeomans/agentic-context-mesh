"""Unit tests for the real SetupService backend (#474).

Every test constructs through the public factory
:func:`kairix.platform.setup.service.build_setup_service` with fakes
injected at the seams BELOW the service via ``SetupServiceDeps`` —
fake provider plugin, recorder persistence, scripted index counters,
tmp-path config files. No monkey-patching, no env mutation
(F1/F2-clean by construction). Failure injection per method follows
the F68 vocabulary: raises / times_out / returns_partial /
returns_empty / unavailable.
"""

from __future__ import annotations

import fcntl
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kairix.credentials import Credentials
from kairix.platform.setup.backends import (
    DEFAULT_VALIDATION_PROBE_MODEL,
    EMBED_COST_USD_PER_1K_TOKENS,
    TOKENS_PER_WORD,
    SetupServiceDeps,
    configured_document_root,
    count_index_chunks,
    embed_lock_held,
    provider_from_credentials,
    run_first_index,
    update_config_file,
    write_config_updates,
)
from kairix.platform.setup.service import SetupService, build_setup_service
from kairix.providers import AuthError, EmbedNotSupported, ProviderNotRegistered, TimeoutExceeded
from tests.fakes import FakePaths, FakeProvider, FakeSearchPipeline

pytestmark = pytest.mark.unit

# Fixture credential value — never a real key.
_FAKE_KEY = "fake-key-for-tests"  # pragma: allowlist secret

_PROBE_OK = {
    "secrets_loaded": True,
    "vector_search_capable": True,
    "bm25_search_capable": True,
    "detail": {},
}


# ---------------------------------------------------------------------------
# Test-local result-row data shapes (BudgetedResult-compatible plain data —
# the pipeline seam returns rows; these are values, not protocol doubles).
# ---------------------------------------------------------------------------


@dataclass
class _FusedRow:
    path: str
    title: str
    snippet: str = ""
    boosted_score: float = 0.0
    rrf_score: float = 0.0


@dataclass
class _ResultRow:
    result: _FusedRow
    content: str = ""


@dataclass
class _Recorder:
    """Recorder for the persistence + config-write seams."""

    persisted: list[tuple[str, str, str]] = field(default_factory=list)
    config_updates: list[dict[str, Any]] = field(default_factory=list)

    def persist(self, api_key: str, endpoint: str, model: str) -> Path | None:
        self.persisted.append((api_key, endpoint, model))
        return Path("/fake/secrets/kairix.env")

    def write_config(self, updates: Any) -> Path:
        self.config_updates.append(dict(updates))
        return Path("/fake/kairix.config.yaml")


def _deps(**overrides: Any) -> SetupServiceDeps:
    """SetupServiceDeps with every seam faked; overrides per scenario."""
    base: dict[str, Any] = {
        "provider_factory": lambda name, creds: FakeProvider(name=name, vector=[0.1] * 8, dim=8),
        "persist_credentials_fn": lambda key, endpoint, model: None,
        "credentials_probe": lambda: False,
        "configured_document_root_fn": lambda: None,
        "write_config_fn": lambda updates: Path("/fake/kairix.config.yaml"),
        "index_counts_fn": lambda db: (0, 0),
        "embed_lock_probe_fn": lambda lock: False,
        "index_runner_fn": lambda: None,
        "search_pipeline_factory": lambda paths: FakeSearchPipeline(),
        "capability_probe_fn": lambda: dict(_PROBE_OK),
        "tools_count_fn": lambda: 12,
        "environ": {},
    }
    base.update(overrides)
    return SetupServiceDeps(**base)


def _service(tmp_path: Path, **overrides: Any) -> SetupService:
    paths = FakePaths(
        document_root=tmp_path / "docs",
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    return build_setup_service(paths=paths, deps=_deps(**overrides))


def _wait_until_not_running(service: SetupService, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while service.index_status().running and time.monotonic() < deadline:
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_all_steps_done(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    service = _service(
        tmp_path,
        credentials_probe=lambda: True,
        configured_document_root_fn=lambda: docs,
        index_counts_fn=lambda db: (42, 0),
    )
    status = service.status()
    assert status.provider_done is True
    assert status.source_done is True
    assert status.index_done is True


def test_status_reports_nothing_done_on_a_fresh_host(tmp_path: Path) -> None:
    service = _service(tmp_path)
    status = service.status()
    assert status.provider_done is False
    assert status.source_done is False
    assert status.index_done is False


def test_status_source_not_done_when_configured_folder_is_missing(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        configured_document_root_fn=lambda: tmp_path / "never-created",
    )
    assert service.status().source_done is False


# ---------------------------------------------------------------------------
# validate_provider
# ---------------------------------------------------------------------------


def test_validate_provider_builds_plugin_from_supplied_values(tmp_path: Path) -> None:
    seen: list[tuple[str, Credentials]] = []

    def factory(name: str, creds: Credentials) -> FakeProvider:
        seen.append((name, creds))
        return FakeProvider(name=name, vector=[0.1] * 8, dim=8)

    service = _service(tmp_path, provider_factory=factory)
    validation = service.validate_provider("openai", _FAKE_KEY, "https://llm.example.test/v1")
    assert validation.ok is True
    assert validation.error is None
    assert validation.models == (DEFAULT_VALIDATION_PROBE_MODEL,)
    ((name, creds),) = seen
    assert name == "openai"
    assert creds.api_key == _FAKE_KEY
    assert creds.endpoint == "https://llm.example.test/v1"


def test_validate_provider_fills_openai_default_endpoint(tmp_path: Path) -> None:
    seen: list[Credentials] = []

    def factory(name: str, creds: Credentials) -> FakeProvider:
        seen.append(creds)
        return FakeProvider(name=name, vector=[0.1] * 8)

    service = _service(tmp_path, provider_factory=factory)
    assert service.validate_provider("openai", _FAKE_KEY, None).ok is True
    assert seen[0].endpoint == "https://api.openai.com/v1"


def test_validate_provider_remaps_azure_pick_by_endpoint_shape(tmp_path: Path) -> None:
    seen: list[str] = []

    def factory(name: str, creds: Credentials) -> FakeProvider:
        seen.append(name)
        return FakeProvider(name=name, vector=[0.1] * 8)

    service = _service(tmp_path, provider_factory=factory)
    # A legacy endpoint must ride azure_legacy even when azure_foundry was picked.
    service.validate_provider("azure_foundry", _FAKE_KEY, "https://res.openai.azure.com")
    # A Foundry endpoint must ride azure_foundry even when azure_legacy was picked.
    service.validate_provider("azure_legacy", _FAKE_KEY, "https://res.services.ai.azure.com")
    assert seen == ["azure_legacy", "azure_foundry"]


def test_validate_provider_requires_endpoint_for_azure(tmp_path: Path) -> None:
    service = _service(tmp_path)
    validation = service.validate_provider("azure_foundry", _FAKE_KEY, "")
    assert validation.ok is False
    assert validation.models == ()
    assert validation.error is not None
    assert "endpoint" in validation.error
    assert "fix:" in validation.error


def test_validate_provider_surfaces_auth_error_verbatim(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(
            embed_raises=AuthError("OpenAI auth rejected (401) for provider 'openai': bad key"),
        ),
    )
    validation = service.validate_provider("openai", _FAKE_KEY, None)
    assert validation.ok is False
    assert validation.error is not None
    assert "OpenAI auth rejected (401)" in validation.error
    assert "fix:" in validation.error
    assert "next:" in validation.error


def test_validate_provider_surfaces_timeout_verbatim(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(
            embed_raises=TimeoutExceeded("embed timed out after 30s"),
        ),
    )
    validation = service.validate_provider("openai", _FAKE_KEY, None)
    assert validation.ok is False
    assert validation.error is not None
    assert "embed timed out after 30s" in validation.error


def test_validate_provider_never_leaks_the_api_key(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """F15 — even when the provider's own error echoes the key, the
    validation error and the logs must not."""
    secret = "fake-secret-do-not-echo-0987654321"  # pragma: allowlist secret
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(
            embed_raises=AuthError(f"rejected credential {secret} at endpoint"),
        ),
    )
    with caplog.at_level(logging.DEBUG):
        validation = service.validate_provider("openai", secret, None)
    assert validation.ok is False
    assert validation.error is not None
    assert secret not in validation.error
    assert "[redacted]" in validation.error
    assert secret not in caplog.text


def test_validate_provider_rejects_empty_embedding(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(embed_empty=True),
    )
    validation = service.validate_provider("openai", _FAKE_KEY, None)
    assert validation.ok is False
    assert validation.error is not None
    assert "empty embedding" in validation.error


def test_validate_provider_falls_back_to_chat_for_chat_only_plugins(tmp_path: Path) -> None:
    provider = FakeProvider(
        embed_raises=EmbedNotSupported(provider_name="anthropic"),
        chat_reply="ok",
    )
    service = _service(tmp_path, provider_factory=lambda name, creds: provider)
    validation = service.validate_provider("anthropic", _FAKE_KEY, None)
    assert validation.ok is True
    assert validation.models == ("claude-3-5-haiku-latest",)
    assert len(provider.chat_calls) == 1


def test_validate_provider_rejects_empty_chat_reply(tmp_path: Path) -> None:
    provider = FakeProvider(
        embed_raises=EmbedNotSupported(provider_name="anthropic"),
        chat_reply="",
    )
    service = _service(tmp_path, provider_factory=lambda name, creds: provider)
    validation = service.validate_provider("anthropic", _FAKE_KEY, None)
    assert validation.ok is False
    assert validation.error is not None
    assert "empty chat reply" in validation.error


def test_validate_provider_probes_the_supplied_azure_deployment(tmp_path: Path) -> None:
    """#484 — the operator's deployment name replaces the probe-model
    literal and is returned as the validated model."""
    seen: list[Credentials] = []

    def factory(name: str, creds: Credentials) -> FakeProvider:
        seen.append(creds)
        return FakeProvider(name=name, vector=[0.1] * 8)

    service = _service(tmp_path, provider_factory=factory)
    validation = service.validate_provider(
        "azure_foundry",
        _FAKE_KEY,
        "https://res.services.ai.azure.com",
        deployment="my-embed-deploy",
    )
    assert validation.ok is True
    assert validation.models == ("my-embed-deploy",)
    assert seen[0].model == "my-embed-deploy"


def test_validate_provider_blank_deployment_keeps_the_default_probe(tmp_path: Path) -> None:
    service = _service(tmp_path)
    validation = service.validate_provider(
        "azure_foundry",
        _FAKE_KEY,
        "https://res.services.ai.azure.com",
        deployment="   ",
    )
    assert validation.ok is True
    assert validation.models == (DEFAULT_VALIDATION_PROBE_MODEL,)


def test_deployment_not_found_reports_the_key_works(tmp_path: Path) -> None:
    """#484 — Azure's DeploymentNotFound means the key authenticated;
    blaming the key would send the operator the wrong way."""
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(
            embed_raises=RuntimeError(
                'Azure Foundry transport error: NotFoundError("Error code: 404 - '
                "{'error': {'code': 'DeploymentNotFound', 'message': 'The API deployment "
                "for this resource does not exist.'}}\")"
            ),
        ),
    )
    validation = service.validate_provider(
        "azure_foundry",
        _FAKE_KEY,
        "https://res.services.ai.azure.com",
        deployment="wrong-name",
    )
    assert validation.ok is False
    assert validation.deployment_missing is True
    assert validation.error is not None
    assert "Your key works" in validation.error
    assert "'wrong-name'" in validation.error
    assert "fix:" in validation.error
    assert "next:" in validation.error
    # The generic "your key may be fine" tail belongs to the key-blame
    # branch, not this one.
    assert "your key may be fine" not in validation.error


def test_deployment_not_found_error_never_leaks_the_key(tmp_path: Path) -> None:
    """F15 — even when the provider echoes the key inside the
    DeploymentNotFound body, the rendered error must not."""
    secret = "fake-secret-do-not-echo-5556667778"  # pragma: allowlist secret
    service = _service(
        tmp_path,
        provider_factory=lambda name, creds: FakeProvider(
            embed_raises=RuntimeError(f"DeploymentNotFound for credential {secret}"),
        ),
    )
    validation = service.validate_provider(
        "azure_foundry",
        secret,
        "https://res.services.ai.azure.com",
        deployment="wrong-name",
    )
    assert validation.deployment_missing is True
    assert validation.error is not None
    assert secret not in validation.error


# ---------------------------------------------------------------------------
# save_provider
# ---------------------------------------------------------------------------


def test_save_provider_persists_credentials_and_writes_config(tmp_path: Path) -> None:
    recorder = _Recorder()
    service = _service(
        tmp_path,
        persist_credentials_fn=recorder.persist,
        write_config_fn=recorder.write_config,
    )
    service.save_provider("openai", _FAKE_KEY, "https://llm.example.test/v1", "model-alpha")
    assert recorder.persisted == [(_FAKE_KEY, "https://llm.example.test/v1", "model-alpha")]
    assert recorder.config_updates == [{"provider": "openai"}]


def test_save_provider_fills_default_endpoint_and_remaps_azure(tmp_path: Path) -> None:
    recorder = _Recorder()
    service = _service(
        tmp_path,
        persist_credentials_fn=recorder.persist,
        write_config_fn=recorder.write_config,
    )
    service.save_provider("openai", _FAKE_KEY, None, None)
    service.save_provider("azure_foundry", _FAKE_KEY, "https://res.openai.azure.com", "embed-3")
    assert recorder.persisted[0] == (_FAKE_KEY, "https://api.openai.com/v1", "")
    assert recorder.persisted[1] == (_FAKE_KEY, "https://res.openai.azure.com", "embed-3")
    assert recorder.config_updates == [{"provider": "openai"}, {"provider": "azure_legacy"}]


def test_save_provider_persists_the_deployment_as_the_embed_model(tmp_path: Path) -> None:
    """#484 — with no chosen model, the Azure deployment name fills the
    embed-model slot so indexing talks to the deployment that validated."""
    recorder = _Recorder()
    service = _service(tmp_path, persist_credentials_fn=recorder.persist)
    service.save_provider(
        "azure_foundry",
        _FAKE_KEY,
        "https://res.services.ai.azure.com",
        None,
        deployment="my-embed-deploy",
    )
    assert recorder.persisted == [(_FAKE_KEY, "https://res.services.ai.azure.com", "my-embed-deploy")]


def test_save_provider_chosen_model_wins_over_the_deployment(tmp_path: Path) -> None:
    recorder = _Recorder()
    service = _service(tmp_path, persist_credentials_fn=recorder.persist)
    service.save_provider(
        "azure_foundry",
        _FAKE_KEY,
        "https://res.services.ai.azure.com",
        "chosen-model",
        deployment="my-embed-deploy",
    )
    assert recorder.persisted == [(_FAKE_KEY, "https://res.services.ai.azure.com", "chosen-model")]


# ---------------------------------------------------------------------------
# scan_folder
# ---------------------------------------------------------------------------


def test_scan_folder_counts_files_and_estimates_words_and_cost(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    (docs / "nested").mkdir(parents=True)
    (docs / "a.md").write_text("word " * 100)
    (docs / "b.txt").write_text("word " * 100)
    (docs / "nested" / "c.markdown").write_text("word " * 100)
    (docs / "ignored.pdf").write_bytes(b"%PDF-")
    service = _service(tmp_path)
    scan = service.scan_folder(str(docs))
    assert scan.ok is True
    assert scan.error is None
    assert scan.files == 3
    assert scan.words_estimate == 300
    assert scan.cost_estimate_usd == round(300 * TOKENS_PER_WORD / 1000.0 * EMBED_COST_USD_PER_1K_TOKENS, 4)


def test_scan_folder_extrapolates_words_beyond_the_sample(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    # More files than the 200-file sample — the average extrapolates.
    for i in range(210):
        (docs / f"note-{i:03d}.md").write_text("word " * 10)
    scan = _service(tmp_path).scan_folder(str(docs))
    assert scan.ok is True
    assert scan.files == 210
    assert scan.words_estimate == 2100


def test_scan_folder_counts_unreadable_files_but_skips_their_words(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("a.md", "b.md"):
        (docs / name).write_text("word " * 50)
    (docs / "ghost.md").symlink_to(docs / "missing-target.md")
    scan = _service(tmp_path).scan_folder(str(docs))
    assert scan.ok is True
    assert scan.files == 3
    # 50-word average over 2 readable files, extrapolated across 3 files.
    assert scan.words_estimate == 150


def test_scan_folder_rejects_missing_folder(tmp_path: Path) -> None:
    scan = _service(tmp_path).scan_folder(str(tmp_path / "nowhere"))
    assert scan.ok is False
    assert scan.files == 0
    assert scan.error is not None
    assert "nowhere" in scan.error
    assert "fix:" in scan.error


def test_scan_folder_rejects_blank_path(tmp_path: Path) -> None:
    scan = _service(tmp_path).scan_folder("   ")
    assert scan.ok is False
    assert scan.error is not None
    assert "No folder path" in scan.error


def test_scan_folder_rejects_a_file_path(tmp_path: Path) -> None:
    target = tmp_path / "single.md"
    target.write_text("not a folder")
    scan = _service(tmp_path).scan_folder(str(target))
    assert scan.ok is False
    assert scan.error is not None


def test_scan_folder_rejects_relative_paths_naming_the_resolution_base(tmp_path: Path) -> None:
    """#486 — silently joining the server cwd surprises operators; the
    rejection names the folder a relative path would resolve against."""
    scan = _service(tmp_path).scan_folder("notes/projects")
    assert scan.ok is False
    assert scan.error is not None
    assert "relative path" in scan.error
    assert str(Path.cwd()) in scan.error
    assert "fix:" in scan.error
    assert "next:" in scan.error


def test_scan_folder_not_found_in_a_container_names_the_mounted_root(tmp_path: Path) -> None:
    """#486 — inside a container the fix: line points at the compose-mounted
    document root as the candidate to try."""
    service = _service(
        tmp_path,
        environ={"KAIRIX_CONTAINER": "1", "KAIRIX_DOCUMENT_ROOT": "/data/documents"},
    )
    scan = service.scan_folder(str(tmp_path / "nowhere"))
    assert scan.ok is False
    assert scan.error is not None
    assert "fix:" in scan.error
    assert "/data/documents" in scan.error
    assert "mounts your documents" in scan.error


def test_scan_folder_not_found_outside_a_container_keeps_the_generic_fix(tmp_path: Path) -> None:
    scan = _service(tmp_path).scan_folder(str(tmp_path / "nowhere"))
    assert scan.ok is False
    assert scan.error is not None
    assert "mounts your documents" not in scan.error


def test_scan_folder_handles_an_empty_folder(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    scan = _service(tmp_path).scan_folder(str(docs))
    assert scan.ok is True
    assert scan.files == 0
    assert scan.words_estimate == 0
    assert scan.cost_estimate_usd == 0.0


# ---------------------------------------------------------------------------
# save_source
# ---------------------------------------------------------------------------


def test_save_source_writes_document_root_into_config(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    recorder = _Recorder()
    service = _service(tmp_path, write_config_fn=recorder.write_config)
    service.save_source(str(docs))
    assert recorder.config_updates == [{"paths": {"document_root": str(docs)}}]


def test_save_source_rejects_missing_folder_with_affordance(tmp_path: Path) -> None:
    recorder = _Recorder()
    service = _service(tmp_path, write_config_fn=recorder.write_config)
    with pytest.raises(ValueError, match="fix:"):
        service.save_source(str(tmp_path / "nowhere"))
    assert recorder.config_updates == []


def test_save_source_rejects_relative_paths_naming_the_resolution_base(tmp_path: Path) -> None:
    recorder = _Recorder()
    service = _service(tmp_path, write_config_fn=recorder.write_config)
    with pytest.raises(ValueError, match="relative path") as excinfo:
        service.save_source("notes/projects")
    assert str(Path.cwd()) in str(excinfo.value)
    assert recorder.config_updates == []


def test_save_source_rejects_a_blank_path(tmp_path: Path) -> None:
    """A blank path expands to ``.`` — without the absolute-path guard it
    would silently persist the server's working directory."""
    recorder = _Recorder()
    service = _service(tmp_path, write_config_fn=recorder.write_config)
    with pytest.raises(ValueError, match="fix:"):
        service.save_source("   ")
    assert recorder.config_updates == []


# ---------------------------------------------------------------------------
# source_hint
# ---------------------------------------------------------------------------


def test_source_hint_prefills_the_mounted_root_in_a_container(tmp_path: Path) -> None:
    service = _service(tmp_path, environ={"KAIRIX_CONTAINER": "1"})
    hint = service.source_hint()
    assert hint.in_container is True
    assert hint.suggested_path == "/data/documents"


def test_source_hint_honours_the_configured_document_root(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        environ={"KAIRIX_CONTAINER": "1", "KAIRIX_DOCUMENT_ROOT": "/srv/knowledge"},
    )
    hint = service.source_hint()
    assert hint.in_container is True
    assert hint.suggested_path == "/srv/knowledge"


def test_source_hint_is_blank_outside_a_container(tmp_path: Path) -> None:
    hint = _service(tmp_path).source_hint()
    assert hint.in_container is False
    assert hint.suggested_path == ""


def test_update_config_file_merges_paths_and_preserves_other_keys(tmp_path: Path) -> None:
    import yaml

    target = tmp_path / "kairix.config.yaml"
    target.write_text(
        "provider: openai\npaths:\n  db_path: /data/index.sqlite\nretrieval:\n  fusion_strategy: bm25_primary\n"
    )
    update_config_file(target, {"paths": {"document_root": "/data/documents"}})
    loaded = yaml.safe_load(target.read_text())
    assert loaded["provider"] == "openai"
    assert loaded["paths"] == {"db_path": "/data/index.sqlite", "document_root": "/data/documents"}
    assert loaded["retrieval"] == {"fusion_strategy": "bm25_primary"}


def test_update_config_file_creates_a_fresh_config(tmp_path: Path) -> None:
    import yaml

    target = tmp_path / "kairix.config.yaml"
    update_config_file(target, {"provider": "anthropic"})
    assert yaml.safe_load(target.read_text()) == {"provider": "anthropic"}


def test_write_config_updates_targets_the_overlay_and_creates_parents(tmp_path: Path) -> None:
    """#485 — with an overlay configured, wizard saves land on the overlay
    file (parents created), never on the read-only base config."""
    import yaml

    base = tmp_path / "etc" / "kairix.config.yaml"
    base.parent.mkdir(parents=True)
    base.write_text("provider: openai\n")
    overlay = tmp_path / "var" / "lib" / "kairix" / "kairix.config.local.yaml"
    written = write_config_updates(
        {"provider": "azure_foundry"},
        overlay_path=str(overlay),
        config_path=str(base),
    )
    assert written == overlay
    assert yaml.safe_load(overlay.read_text()) == {"provider": "azure_foundry"}
    # The base config is untouched.
    assert yaml.safe_load(base.read_text()) == {"provider": "openai"}


def test_write_config_updates_merges_into_the_existing_overlay(tmp_path: Path) -> None:
    import yaml

    overlay = tmp_path / "kairix.config.local.yaml"
    write_config_updates({"provider": "azure_foundry"}, overlay_path=str(overlay), config_path=None)
    write_config_updates(
        {"paths": {"document_root": "/data/documents"}},
        overlay_path=str(overlay),
        config_path=None,
    )
    loaded = yaml.safe_load(overlay.read_text())
    assert loaded["provider"] == "azure_foundry"
    assert loaded["paths"] == {"document_root": "/data/documents"}


def test_write_config_updates_falls_back_to_the_single_file(tmp_path: Path) -> None:
    import yaml

    target = tmp_path / "kairix.config.yaml"
    written = write_config_updates({"provider": "openai"}, overlay_path=None, config_path=str(target))
    assert written == target
    assert yaml.safe_load(target.read_text()) == {"provider": "openai"}


# ---------------------------------------------------------------------------
# start_index / index_status
# ---------------------------------------------------------------------------


def test_start_index_runs_in_background_then_reports_done(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    def runner() -> None:
        started.set()
        assert release.wait(timeout=5)

    service = _service(tmp_path, index_runner_fn=runner, index_counts_fn=lambda db: (4, 0))
    service.start_index()
    assert started.wait(timeout=5)
    assert service.index_status().running is True
    release.set()
    _wait_until_not_running(service)
    final = service.index_status()
    assert final.running is False
    assert final.done is True
    assert final.chunks_done == 4
    assert final.chunks_total == 4
    assert final.error is None


def test_start_index_is_idempotent_while_running(tmp_path: Path) -> None:
    calls: list[int] = []
    release = threading.Event()

    def runner() -> None:
        calls.append(1)
        assert release.wait(timeout=5)

    service = _service(tmp_path, index_runner_fn=runner)
    service.start_index()
    service.start_index()
    release.set()
    _wait_until_not_running(service)
    assert len(calls) == 1


def test_index_failure_surfaces_in_status(tmp_path: Path) -> None:
    def runner() -> None:
        raise RuntimeError("provider rejected the embed request")

    service = _service(tmp_path, index_runner_fn=runner, index_counts_fn=lambda db: (0, 9))
    service.start_index()
    _wait_until_not_running(service)
    status = service.index_status()
    assert status.done is False
    assert status.error is not None
    assert "Indexing stopped" in status.error
    assert "provider rejected the embed request" in status.error
    assert "fix:" in status.error


def test_concurrent_lock_exit_reports_a_friendly_error(tmp_path: Path) -> None:
    def runner() -> None:
        raise SystemExit(3)

    service = _service(tmp_path, index_runner_fn=runner)
    service.start_index()
    _wait_until_not_running(service)
    status = service.index_status()
    assert status.error is not None
    assert "another indexing run is already in progress" in status.error


def test_external_lock_holder_reports_running_without_spawning(tmp_path: Path) -> None:
    calls: list[int] = []
    service = _service(
        tmp_path,
        index_runner_fn=lambda: calls.append(1),
        embed_lock_probe_fn=lambda lock: True,
        index_counts_fn=lambda db: (2, 8),
    )
    service.start_index()
    status = service.index_status()
    assert calls == []
    assert status.running is True
    assert status.done is False
    assert status.chunks_done == 2
    assert status.chunks_total == 10


def test_index_status_not_done_while_chunks_are_pending(tmp_path: Path) -> None:
    service = _service(tmp_path, index_counts_fn=lambda db: (3, 7))
    status = service.index_status()
    assert status.running is False
    assert status.done is False
    assert status.chunks_done == 3
    assert status.chunks_total == 10


def test_index_status_on_a_fresh_database_is_not_done(tmp_path: Path) -> None:
    status = _service(tmp_path).index_status()
    assert status.running is False
    assert status.done is False
    assert status.chunks_done == 0
    assert status.chunks_total == 0


def test_run_first_index_passes_when_no_chunks_failed() -> None:
    calls: list[dict[str, Any]] = []

    def pipeline(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(failed=0, embedded=12)

    run_first_index(pipeline_fn=pipeline)
    assert calls == [{"skip_recall_check": True}]


def test_run_first_index_raises_with_affordance_on_failed_chunks() -> None:
    def pipeline(**kwargs: Any) -> Any:
        return SimpleNamespace(failed=3, embedded=9)

    with pytest.raises(RuntimeError, match="3 chunks failed to embed"):
        run_first_index(pipeline_fn=pipeline)


# ---------------------------------------------------------------------------
# count_index_chunks / embed_lock_held (the production counter + lock probes)
# ---------------------------------------------------------------------------


def test_count_index_chunks_reads_embedded_and_pending(tmp_path: Path) -> None:
    from kairix.core.db.schema import create_schema

    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.execute("INSERT INTO documents (collection, path, title, hash, active) VALUES ('default', 'a.md', 'A', 'h1', 1)")
    db.execute("INSERT INTO content (hash, doc) VALUES ('h1', 'embedded text')")
    db.execute("INSERT INTO content_vectors (hash, seq, pos, model) VALUES ('h1', 0, 0, 'embed-model')")
    db.execute("INSERT INTO documents (collection, path, title, hash, active) VALUES ('default', 'b.md', 'B', 'h2', 1)")
    db.execute("INSERT INTO content (hash, doc) VALUES ('h2', 'pending text')")
    db.commit()
    db.close()
    assert count_index_chunks(db_path) == (1, 1)


def test_count_index_chunks_missing_db_reads_zero(tmp_path: Path) -> None:
    assert count_index_chunks(tmp_path / "never-created.sqlite") == (0, 0)


def test_count_index_chunks_uninitialised_db_reads_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.sqlite"
    sqlite3.connect(str(db_path)).close()
    assert count_index_chunks(db_path) == (0, 0)


def test_count_index_chunks_unopenable_db_reads_zero(tmp_path: Path) -> None:
    # A directory at the db path exists but cannot be opened as a database.
    blocked = tmp_path / "index.sqlite"
    blocked.mkdir()
    assert count_index_chunks(blocked) == (0, 0)


def test_embed_lock_held_tracks_the_flock(tmp_path: Path) -> None:
    lockfile = tmp_path / "embed.lock"
    assert embed_lock_held(lockfile) is False  # no lockfile yet
    lockfile.touch()
    assert embed_lock_held(lockfile) is False  # present but unheld
    holder = open(lockfile, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert embed_lock_held(lockfile) is True
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()
    assert embed_lock_held(lockfile) is False


# ---------------------------------------------------------------------------
# first_search
# ---------------------------------------------------------------------------


def test_first_search_maps_top_five_with_relative_scores(tmp_path: Path) -> None:
    rows = [
        _ResultRow(
            result=_FusedRow(path=f"notes/doc-{i}.md", title=f"Note {i}", boosted_score=score),
            content=f"snippet for note {i}",
        )
        for i, score in enumerate([10.0, 8.0, 6.0, 4.0, 2.0, 1.0, 0.5])
    ]
    service = _service(
        tmp_path,
        search_pipeline_factory=lambda paths: FakeSearchPipeline(scripted_results=rows),
    )
    preview = service.first_search("project kickoff")
    assert len(preview.results) == 5
    top = preview.results[0]
    assert top.title == "Note 0"
    assert top.source == "notes/doc-0.md"
    assert top.snippet == "snippet for note 0"
    assert top.score == 1.0
    assert preview.results[1].score == pytest.approx(0.8)
    assert preview.results[4].score == pytest.approx(0.2)


def test_first_search_falls_back_to_rrf_score_and_snippet(tmp_path: Path) -> None:
    rows = [
        _ResultRow(
            result=_FusedRow(path="notes/a.md", title="A", snippet="fused snippet", rrf_score=0.03),
            content="",
        )
    ]
    service = _service(
        tmp_path,
        search_pipeline_factory=lambda paths: FakeSearchPipeline(scripted_results=rows),
    )
    preview = service.first_search("anything")
    assert preview.results[0].snippet == "fused snippet"
    assert preview.results[0].score == 1.0


def test_first_search_returns_empty_preview_when_index_has_nothing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    assert service.first_search("anything").results == ()
    assert service.first_search("").results == ()


def test_first_search_returns_empty_preview_when_pipeline_unavailable(tmp_path: Path) -> None:
    def boom(paths: Any) -> Any:
        raise RuntimeError("no provider configured")

    service = _service(tmp_path, search_pipeline_factory=boom)
    assert service.first_search("anything").results == ()


# ---------------------------------------------------------------------------
# agent_connect_info
# ---------------------------------------------------------------------------


def test_agent_connect_info_uses_the_default_endpoint(tmp_path: Path) -> None:
    info = _service(tmp_path).agent_connect_info()
    assert info.mcp_url == "http://localhost:8080/mcp"
    clients = [snippet.client for snippet in info.snippets]
    assert any("Claude Code" in client for client in clients)
    assert any("OpenClaw" in client for client in clients)
    assert any("Generic MCP" in client for client in clients)
    import json

    # Exact shapes from docs/getting-started/connecting-agents.md.
    claude_code = next(s for s in info.snippets if "Claude Code" in s.client)
    assert json.loads(claude_code.config_text) == {
        "mcpServers": {"kairix": {"type": "http", "url": "http://localhost:8080/mcp"}}
    }
    openclaw = next(s for s in info.snippets if "OpenClaw" in s.client)
    assert json.loads(openclaw.config_text) == {
        "mcp": {
            "servers": {
                "mcp-kairix": {
                    "command": "kairix",
                    "args": ["mcp", "serve"],
                    "description": "Knowledge base search, research, entity lookup",
                }
            }
        }
    }
    generic = next(s for s in info.snippets if "Generic MCP" in s.client)
    assert generic.config_text == "http://localhost:8080/mcp"


def test_agent_connect_info_honours_the_endpoint_override(tmp_path: Path) -> None:
    service = _service(tmp_path, environ={"KAIRIX_MCP_ENDPOINT": "http://localhost:9191/mcp"})
    info = service.agent_connect_info()
    assert info.mcp_url == "http://localhost:9191/mcp"
    assert all("9191" in s.config_text for s in info.snippets if "url" in s.config_text)


# ---------------------------------------------------------------------------
# verify_agent_handshake
# ---------------------------------------------------------------------------


def test_handshake_reports_tool_count_when_healthy(tmp_path: Path) -> None:
    result = _service(tmp_path, tools_count_fn=lambda: 35).verify_agent_handshake()
    assert result.ok is True
    assert result.tools_count == 35
    assert result.error is None


def test_handshake_fails_when_no_tools_registered(tmp_path: Path) -> None:
    result = _service(tmp_path, tools_count_fn=lambda: 0).verify_agent_handshake()
    assert result.ok is False
    assert result.tools_count == 0
    assert result.error is not None
    assert "no tools" in result.error
    assert "fix:" in result.error


def test_handshake_fails_when_credentials_are_not_loaded(tmp_path: Path) -> None:
    detail = {"secrets_loaded": "LLM credentials missing"}  # pragma: allowlist secret — capability key, not a value
    probe = {
        "secrets_loaded": False,
        "vector_search_capable": False,
        "bm25_search_capable": True,
        "detail": detail,
    }
    result = _service(tmp_path, capability_probe_fn=lambda: probe).verify_agent_handshake()
    assert result.ok is False
    assert result.tools_count == 12
    assert result.error is not None
    assert "LLM credentials missing" in result.error


def test_handshake_credential_failure_without_detail_stays_readable(tmp_path: Path) -> None:
    probe = {"secrets_loaded": False, "vector_search_capable": False, "bm25_search_capable": True, "detail": {}}
    result = _service(tmp_path, capability_probe_fn=lambda: probe).verify_agent_handshake()
    assert result.ok is False
    assert result.error is not None
    assert "no detail reported" in result.error


def test_index_status_resolves_the_platform_db_path_when_paths_omitted() -> None:
    """Production callers pass no ``paths`` — the db path resolves through
    the platform chain and flows into the (injected) counters."""
    seen: list[Path] = []

    def counts(db: Path) -> tuple[int, int]:
        seen.append(db)
        return (0, 0)

    service = build_setup_service(deps=_deps(index_counts_fn=counts))
    status = service.index_status()
    assert status.running is False
    assert seen and seen[0].name  # a concrete platform-resolved path arrived


def test_handshake_fails_when_the_probe_is_unavailable(tmp_path: Path) -> None:
    def boom() -> dict[str, Any]:
        raise RuntimeError("probe stack not importable")

    result = _service(tmp_path, capability_probe_fn=boom).verify_agent_handshake()
    assert result.ok is False
    assert result.tools_count == 0
    assert result.error is not None
    assert "probe stack not importable" in result.error


# ---------------------------------------------------------------------------
# configured_document_root / provider_from_credentials (public building blocks)
# ---------------------------------------------------------------------------


def test_configured_document_root_prefers_the_env_override() -> None:
    root = configured_document_root(override="/data/docs", config_paths={"document_root": "/cfg/docs"})
    assert root == Path("/data/docs")


def test_configured_document_root_falls_back_to_config() -> None:
    root = configured_document_root(override=None, config_paths={"document_root": "/cfg/docs"})
    assert root == Path("/cfg/docs")


def test_configured_document_root_is_none_when_unconfigured() -> None:
    assert configured_document_root(override=None, config_paths={}) is None
    assert configured_document_root(override="", config_paths={"db_path": "/x"}) is None


def test_provider_from_credentials_threads_explicit_values() -> None:
    """Real entry-point resolution: the installed openai plugin must be
    constructed against the SUPPLIED credentials, proven through the
    public dimension() surface (dims travel inside Credentials)."""
    credentials = Credentials(
        api_key=_FAKE_KEY,
        endpoint="https://llm.example.test/v1",
        model="embed-model",
        dims=7,
    )
    provider = provider_from_credentials("openai", credentials)
    assert provider.name == "openai"
    assert provider.dimension() == 7


def test_provider_from_credentials_rejects_unknown_plugins() -> None:
    credentials = Credentials(api_key=_FAKE_KEY, endpoint="", model="m")
    with pytest.raises(ProviderNotRegistered) as excinfo:
        provider_from_credentials("no-such-plugin", credentials)
    assert "no-such-plugin" in str(excinfo.value)
    assert "openai" in excinfo.value.available


def test_provider_from_credentials_calls_bare_factories_without_the_seam() -> None:
    """Factories without a credentials_resolver parameter (bedrock-style,
    credentials ride the platform chain) are called with no arguments."""
    built = FakeProvider(name="plain")

    class _EntryPoint:
        name = "plain"

        @staticmethod
        def load() -> Any:
            return lambda: built

    def fake_entry_points(*, group: str, name: str | None = None) -> list[Any]:
        return [_EntryPoint()]

    credentials = Credentials(api_key=_FAKE_KEY, endpoint="", model="m")
    provider = provider_from_credentials("plain", credentials, entry_points=fake_entry_points)
    assert provider is built


# ---------------------------------------------------------------------------
# Capability tour (#490) — tour_prep / tour_remember_roundtrip /
# tour_brief / tour_timeline passthroughs, driven through _deps(**overrides)
# with the real use-case value objects scripted at the seams.
# ---------------------------------------------------------------------------


def _prep_output(**overrides: Any) -> Any:
    from kairix.use_cases.prep import PrepOutput

    base: dict[str, Any] = {"query": "projects", "tier": "l0"}
    base.update(overrides)
    return PrepOutput(**base)


def _remember_result(**overrides: Any) -> Any:
    from kairix.use_cases.remember import RememberResult

    base: dict[str, Any] = {
        "path": "/data/documents/04-Agent-Knowledge/agent-alpha/2026-06-11-setup-finished.md",
        "agent": "agent-alpha",
        "kind": "note",
        "classified_as": "unknown",
        "indexed": True,
    }
    base.update(overrides)
    return RememberResult(**base)


def _brief_output(**overrides: Any) -> Any:
    from kairix.use_cases.brief import BriefOutput

    base: dict[str, Any] = {"agent": "agent-alpha"}
    base.update(overrides)
    return BriefOutput(**base)


def _timeline_result(**overrides: Any) -> Any:
    from kairix.use_cases.timeline import TimelineResult

    base: dict[str, Any] = {
        "original_query": "last week",
        "rewritten_query": "last week",
        "is_temporal": True,
        "fell_back": False,
        "time_window": {},
    }
    base.update(overrides)
    return TimelineResult(**base)


# --- agent resolution -------------------------------------------------------


def test_tour_remember_uses_the_first_configured_agent(tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_remember(agent: str, content: str) -> Any:
        seen.append(agent)
        return _remember_result(agent=agent)

    service = _service(
        tmp_path,
        remember_fn=fake_remember,
        top_level_config_fn=lambda: {"agents": {"agent-alpha": {}, "agent-beta": {}}},
    )
    service.tour_remember_roundtrip("Setup finished today.")
    assert seen == ["agent-alpha"]


def test_tour_remember_reads_the_legacy_list_schema_in_order(tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_remember(agent: str, content: str) -> Any:
        seen.append(agent)
        return _remember_result(agent=agent)

    service = _service(
        tmp_path,
        remember_fn=fake_remember,
        top_level_config_fn=lambda: {"agents": [{"name": "agent-beta"}, {"name": "agent-alpha"}]},
    )
    service.tour_remember_roundtrip("Setup finished today.")
    assert seen == ["agent-beta"]


def test_tour_falls_back_to_the_shared_agent_without_configured_agents(tmp_path: Path) -> None:
    """A fresh install has no agents: block — the tour rides the legacy
    shared agent, which the config-driven allowlist always accepts."""
    seen: list[str] = []

    def fake_remember(agent: str, content: str) -> Any:
        seen.append(agent)
        return _remember_result(agent=agent)

    service = _service(tmp_path, remember_fn=fake_remember, top_level_config_fn=lambda: None)
    service.tour_remember_roundtrip("Setup finished today.")
    assert seen == ["shared"]


def test_tour_agent_ignores_a_malformed_agents_block(tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_brief(agent: str) -> Any:
        seen.append(agent)
        return _brief_output(agent=agent, content="x", preview="x")

    service = _service(
        tmp_path,
        brief_fn=fake_brief,
        top_level_config_fn=lambda: {"agents": "not-a-block"},
    )
    service.tour_brief()
    assert seen == ["shared"]


# --- tour_prep ---------------------------------------------------------------


def test_tour_prep_passes_the_query_and_maps_summary_and_sources(tmp_path: Path) -> None:
    seen: list[str] = []

    def fake_prep(query: str) -> Any:
        seen.append(query)
        return _prep_output(summary="The rollout is the main thread.", sources=["notes/kickoff.md"])

    service = _service(tmp_path, prep_fn=fake_prep)
    result = service.tour_prep("current projects")
    assert seen == ["current projects"]
    assert result.summary == "The rollout is the main thread."
    assert result.sources == ("notes/kickoff.md",)
    assert result.message == ""


def test_tour_prep_use_case_error_becomes_guidance_without_the_class_name(tmp_path: Path) -> None:
    service = _service(tmp_path, prep_fn=lambda query: _prep_output(error="ValueError: provider exploded"))
    result = service.tour_prep("current projects")
    assert result.summary == ""
    assert "fix:" in result.message
    assert "next:" in result.message
    assert "ValueError" not in result.message


def test_tour_prep_raises_returns_guidance(tmp_path: Path) -> None:
    def boom(query: str) -> Any:
        raise RuntimeError("kaput")

    service = _service(tmp_path, prep_fn=boom)
    result = service.tour_prep("current projects")
    assert result.message != ""
    assert "kaput" not in result.message


def test_tour_prep_empty_corpus_passes_the_honest_summary_through(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        prep_fn=lambda query: _prep_output(summary="No relevant documents found for this topic."),
    )
    result = service.tour_prep("anything")
    assert result.summary == "No relevant documents found for this topic."
    assert result.sources == ()
    assert result.message == ""


# --- tour_remember_roundtrip -------------------------------------------------


def test_tour_remember_roundtrip_finds_the_memory_through_the_search_leg(tmp_path: Path) -> None:
    from tests.fakes import FakeSearchPipeline

    memory_path = "/data/documents/04-Agent-Knowledge/shared/2026-06-11-setup-finished.md"
    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(
                path="04-Agent-Knowledge/shared/2026-06-11-setup-finished.md",
                title="Setup finished",
                content="Setup finished today — this knowledge store is live.",
            ),
        ],
    )
    service = _service(
        tmp_path,
        remember_fn=lambda agent, content: _remember_result(path=memory_path, agent=agent),
        search_pipeline_factory=lambda paths: pipeline,
        top_level_config_fn=lambda: None,
    )
    result = service.tour_remember_roundtrip("Setup finished today.")
    assert result.saved is True
    assert result.found is True
    assert result.path == memory_path
    assert result.elapsed_ms >= 0
    assert len(result.hits) == 1
    assert "2026-06-11-setup-finished.md" in result.hits[0].source
    # The search leg ran against the memory's own content.
    assert pipeline.calls and pipeline.calls[0]["query"] == "Setup finished today."


def test_tour_remember_roundtrip_reports_not_found_when_search_misses(tmp_path: Path) -> None:
    from tests.fakes import FakeSearchPipeline

    pipeline = FakeSearchPipeline(
        scripted_results=[
            FakeSearchPipeline.make_chunk_row(path="notes/other.md", title="Other", content="unrelated"),
        ],
    )
    service = _service(
        tmp_path,
        remember_fn=lambda agent, content: _remember_result(),
        search_pipeline_factory=lambda paths: pipeline,
    )
    result = service.tour_remember_roundtrip("Setup finished today.")
    assert result.saved is True
    assert result.found is False


def test_tour_remember_invalid_agent_returns_agents_block_guidance(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        remember_fn=lambda agent, content: _remember_result(path="", error="InvalidAgent: nope"),
    )
    result = service.tour_remember_roundtrip("Setup finished today.")
    assert result.saved is False
    assert result.found is False
    assert "agents: section of kairix.config.yaml" in result.message
    assert "InvalidAgent" not in result.message


def test_tour_remember_write_failure_returns_guidance(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        remember_fn=lambda agent, content: _remember_result(path="", error="WriteFailed: disk full"),
    )
    result = service.tour_remember_roundtrip("Setup finished today.")
    assert result.saved is False
    assert "fix:" in result.message
    assert "disk full" not in result.message


def test_tour_remember_raises_returns_guidance(tmp_path: Path) -> None:
    def boom(agent: str, content: str) -> Any:
        raise OSError("read-only file system")

    service = _service(tmp_path, remember_fn=boom)
    result = service.tour_remember_roundtrip("Setup finished today.")
    assert result.saved is False
    assert "fix:" in result.message


# --- tour_brief --------------------------------------------------------------


def test_tour_brief_maps_preview_and_next_action(tmp_path: Path) -> None:
    from kairix.core.health import KairixHealth

    service = _service(
        tmp_path,
        brief_fn=lambda agent: _brief_output(
            agent=agent,
            content="full briefing",
            preview="Recent activity: two decisions landed.",
            health=KairixHealth(next_action="All healthy."),
        ),
        top_level_config_fn=lambda: {"agents": {"agent-alpha": {}}},
    )
    result = service.tour_brief()
    assert result.agent == "agent-alpha"
    assert result.preview == "Recent activity: two decisions landed."
    assert result.next_action == "All healthy."
    assert result.message == ""


def test_tour_brief_invalid_agent_returns_agents_block_guidance(tmp_path: Path) -> None:
    """The brief use case only accepts its built-in agent set today — a
    fresh install's shared fallback is rejected, and the tour renders
    honest guidance instead of the use case's internal error string."""
    service = _service(
        tmp_path,
        brief_fn=lambda agent: _brief_output(agent=agent, error="InvalidAgent: 'shared'."),
        top_level_config_fn=lambda: None,
    )
    result = service.tour_brief()
    assert result.preview == ""
    assert "named agent" in result.message
    assert "agents: section of kairix.config.yaml" in result.message
    assert "InvalidAgent" not in result.message


def test_tour_brief_empty_content_passes_through_with_next_action(tmp_path: Path) -> None:
    """Chat offline → the use case returns an empty body plus a health
    directive; the tour passes both through so the screen stays honest."""
    from kairix.core.health import KairixHealth

    service = _service(
        tmp_path,
        brief_fn=lambda agent: _brief_output(
            agent=agent,
            health=KairixHealth(chat="offline", next_action="Use the search tool for now."),
        ),
    )
    result = service.tour_brief()
    assert result.preview == ""
    assert result.message == ""
    assert result.next_action == "Use the search tool for now."


def test_tour_brief_other_errors_return_guidance(tmp_path: Path) -> None:
    service = _service(tmp_path, brief_fn=lambda agent: _brief_output(agent=agent, error="RuntimeError: boom"))
    result = service.tour_brief()
    assert "fix:" in result.message
    assert "RuntimeError" not in result.message


def test_tour_brief_raises_returns_guidance(tmp_path: Path) -> None:
    def boom(agent: str) -> Any:
        raise TimeoutError("too slow")

    service = _service(tmp_path, brief_fn=boom)
    result = service.tour_brief()
    assert "fix:" in result.message
    assert "too slow" not in result.message


# --- tour_timeline -----------------------------------------------------------


def test_tour_timeline_maps_hits_with_dates(tmp_path: Path) -> None:
    from kairix.use_cases.timeline import TimelineHit

    seen: list[str] = []

    def fake_timeline(query: str) -> Any:
        seen.append(query)
        return _timeline_result(
            results=[
                TimelineHit(
                    path="daily/2026-06-08.md",
                    title="Sprint planning",
                    snippet="rollout starts next sprint",
                    score=0.9,
                    date="2026-06-08",
                ),
            ],
        )

    service = _service(tmp_path, timeline_fn=fake_timeline)
    result = service.tour_timeline("last week")
    assert seen == ["last week"]
    assert result.message == ""
    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.source == "daily/2026-06-08.md"
    assert hit.title == "Sprint planning"
    assert hit.date == "2026-06-08"


def test_tour_timeline_caps_the_hit_count(tmp_path: Path) -> None:
    from kairix.platform.setup.backends import TOUR_TIMELINE_TOP_N
    from kairix.use_cases.timeline import TimelineHit

    rows = [
        TimelineHit(path=f"daily/2026-06-{i:02d}.md", title=f"Day {i}", snippet="…", score=0.5)
        for i in range(1, TOUR_TIMELINE_TOP_N + 4)
    ]
    service = _service(tmp_path, timeline_fn=lambda query: _timeline_result(results=rows))
    result = service.tour_timeline("June")
    assert len(result.hits) == TOUR_TIMELINE_TOP_N


def test_tour_timeline_error_returns_guidance(tmp_path: Path) -> None:
    service = _service(tmp_path, timeline_fn=lambda query: _timeline_result(error="ValueError: bad date"))
    result = service.tour_timeline("last week")
    assert result.hits == ()
    assert "fix:" in result.message
    assert "ValueError" not in result.message


def test_tour_timeline_raises_returns_guidance(tmp_path: Path) -> None:
    def boom(query: str) -> Any:
        raise RuntimeError("kaput")

    service = _service(tmp_path, timeline_fn=boom)
    result = service.tour_timeline("last week")
    assert result.hits == ()
    assert "fix:" in result.message
