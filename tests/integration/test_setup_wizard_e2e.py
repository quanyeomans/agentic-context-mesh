"""End-to-end integration tests for the terminal setup wizard.

Wires the production ``run_setup`` orchestrator through real templates,
real merge-writing, and — where it matters — the REAL SetupService
backend constructed via :func:`build_setup_service` with fakes injected
at the seams below the service (``SetupServiceDeps``). ``tmp_path`` is
the destination for both the config file and the document root — no
env-var monkeypatching (F4-clean).

What's covered here that unit + BDD don't catch:
  - The full non-interactive happy-path lands a YAML config with the
    documented top-level keys (``paths``, ``retrieval``, ``provider``,
    ``collections``, ``graph``).
  - A second invocation against the SAME output path merges cleanly
    (re-running setup is idempotent on the destination file).
  - The structured failure shape: a missing document root produces
    ``run_setup -> False`` AND no config file is written.
  - #review-H3 crash regression: a ``SystemExit`` from the index run
    (the embed lock's documented contention behaviour) lands in the
    wizard's status report instead of killing the process — proven
    against the real backend worker through the public CLI surface.
  - M6 scan parity: the numbers the terminal prints equal the backend's
    scan of the same corpus.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from kairix.platform.setup.prompts import SetupContext
from kairix.platform.setup.wizard import WizardDeps, run_setup
from tests.fakes import FakeSetupService

pytestmark = pytest.mark.integration


@pytest.fixture
def doc_root(tmp_path: Path) -> Iterator[Path]:
    """A populated documents directory under tmp_path."""
    root = tmp_path / "docs"
    root.mkdir()
    (root / "intro.md").write_text("# Intro\nHello world.", encoding="utf-8")
    (root / "guide.md").write_text("# Guide\nUseful content here.", encoding="utf-8")
    yield root


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "kairix.config.yaml"


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / ".setup-state.json"


def _deps(tmp_path: Path, service: Any) -> WizardDeps:
    return WizardDeps(
        setup_service=lambda: service,
        persist_credentials=lambda *_a: tmp_path / "unwritten-kairix.env",
        index_poll_seconds=0.0,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_setup_happy_path_writes_well_formed_config(
    doc_root: Path, output_path: Path, state_path: Path, tmp_path: Path
) -> None:
    """Non-interactive happy path: valid doc root + a service whose
    validation passes → ``run_setup`` returns True, the YAML config is
    on disk, parses cleanly, and carries the documented top-level keys.

    Sabotage: if the wizard stopped emitting the ``retrieval`` section
    (template-key wiring regressed), the ``"retrieval" in config``
    assertion would fail. If it stopped writing the file at all on
    success, ``output_path.exists()`` would be False.
    """
    service = FakeSetupService()
    ctx = SetupContext(interactive=False, json_mode=False, state_path=state_path)

    success = run_setup(
        output_path=str(output_path),
        ctx=ctx,
        preset="technical",
        document_path=str(doc_root),
        deps=_deps(tmp_path, service),
    )

    assert success is True
    assert output_path.exists()

    config: dict[str, Any] = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    # Documented top-level keys (provider + paths + retrieval are
    # unconditional — a provider-less config fails at factory
    # construction, #474 defect 1).
    assert config.get("provider") == "azure_foundry", f"provider missing/wrong: {config}"
    assert "paths" in config
    assert "retrieval" in config
    # paths.document_root points at the tmp doc dir, not at a default.
    assert config["paths"]["document_root"] == str(doc_root)
    # The emitted config passes the same schema validation
    # `kairix config validate` runs.
    from kairix.core.search.config_validator import validate_config

    assert validate_config(config) == []
    # The wizard validated the credentials through the service exactly once.
    assert len(service.validate_calls) == 1


def test_setup_rerun_against_same_output_merges_cleanly(
    doc_root: Path, output_path: Path, state_path: Path, tmp_path: Path
) -> None:
    """Two successive setup runs against the same output path produce a
    single, well-formed config file on disk. Re-running setup is a
    common operator move (changing presets, switching doc roots); the
    second run must not corrupt the file or fail.

    Sabotage: if the merge write opened the file in append mode, the
    second run would produce a non-YAML blob (two concatenated documents
    with shared keys) and ``yaml.safe_load`` would either fail or return
    a non-dict.
    """
    ctx = SetupContext(interactive=False, json_mode=False, state_path=state_path)

    run_setup(
        output_path=str(output_path),
        ctx=ctx,
        preset="general",
        document_path=str(doc_root),
        deps=_deps(tmp_path, FakeSetupService()),
    )
    first_size = output_path.stat().st_size

    success_2 = run_setup(
        output_path=str(output_path),
        ctx=ctx,
        preset="technical",
        document_path=str(doc_root),
        deps=_deps(tmp_path, FakeSetupService()),
    )

    assert success_2 is True
    # File is still a single YAML mapping (not appended).
    config: dict[str, Any] = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    assert "paths" in config
    # And it wasn't doubled in size by accidental append.
    assert output_path.stat().st_size <= first_size * 2


def test_setup_rejects_missing_document_root_and_writes_no_config(
    tmp_path: Path, output_path: Path, state_path: Path
) -> None:
    """Structured-failure shape: a document_path that doesn't exist
    causes ``run_setup`` to return False AND skip the config write —
    through the REAL backend scan, so the rejection words match the web
    wizard's for the same path.

    Sabotage: if the wizard started defaulting to ``~/Documents`` on
    missing-dir input (instead of stopping), it would continue, write
    the config, and ``output_path.exists()`` would fail this test.
    """
    from kairix.platform.setup.service import build_setup_service

    ctx = SetupContext(interactive=False, json_mode=False, state_path=state_path)
    missing = tmp_path / "this-dir-does-not-exist"

    success = run_setup(
        output_path=str(output_path),
        ctx=ctx,
        preset="general",
        document_path=str(missing),
        deps=WizardDeps(setup_service=lambda: build_setup_service()),
    )

    assert success is False
    assert not output_path.exists(), "Wizard must not persist a config when doc_root is invalid"


def test_setup_scan_numbers_match_backend_scan_of_same_corpus(
    tmp_path: Path, output_path: Path, state_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """M6 parity: the file count, word estimate, and one-time cost the
    terminal prints are the BACKEND's scan numbers for the same corpus —
    not a terminal-side re-count.

    Sabotage: if the wizard reverted to its own ``**/*.md``-only count
    (the old ``count_documents``), the ``.txt`` file below would be
    missed and the printed count would diverge from the backend's.
    """
    from kairix.platform.setup.service import build_setup_service

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "a.md").write_text("alpha beta gamma " * 40, encoding="utf-8")
    (corpus / "b.md").write_text("delta epsilon " * 25, encoding="utf-8")
    (corpus / "c.txt").write_text("zeta eta theta " * 10, encoding="utf-8")

    expected = build_setup_service().scan_folder(str(corpus))
    assert expected.ok and expected.files == 3

    ctx = SetupContext(interactive=False, json_mode=False, state_path=state_path)
    success = run_setup(
        output_path=str(output_path),
        ctx=ctx,
        preset="general",
        document_path=str(corpus),
        deps=WizardDeps(setup_service=lambda: build_setup_service()),
    )
    assert success is True
    out = capsys.readouterr().out
    assert f"Found: {expected.files:,} documents" in out, f"file count diverges from backend scan:\n{out}"
    assert f"~{expected.words_estimate:,} words" in out, f"word estimate diverges from backend scan:\n{out}"


def test_setup_survives_system_exit_from_real_index_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """#review-H3 crash regression, against the REAL backend worker and
    the public CLI surface: a ``SystemExit`` from the index run (the
    embed lock's documented behaviour when another embed holds the lock
    for the whole wait window) must NOT escape and kill the wizard — the
    run finishes, exit code 0, and the operator sees the lock guidance.

    Pre-fix shape: the terminal wizard invoked the embed CLI ``main()``
    directly under ``except Exception``, so ``SystemExit`` (argparse's
    exit-2 under the dispatcher, AND the embed CLI's own ``sys.exit`` on
    success) escaped at "Indexing..." and killed setup.
    """
    from kairix.platform.setup.backends import SetupServiceDeps
    from kairix.platform.setup.cli import main as setup_cli_main
    from kairix.platform.setup.service import build_setup_service
    from tests.fakes import FakePaths, FakeProvider

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "note.md").write_text("# note\nbody words here", encoding="utf-8")
    output = tmp_path / "kairix.config.yaml"

    def _lock_contention_exit() -> None:
        # acquire_lock exhausts its wait window with sys.exit(3).
        sys.exit(3)

    paths = FakePaths(
        document_root=docs,
        db_path=tmp_path / "index.sqlite",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    service = build_setup_service(
        paths=paths,
        deps=SetupServiceDeps(
            provider_factory=lambda name, creds: FakeProvider(name=name, vector=[0.1] * 8, dim=8),
            index_runner_fn=_lock_contention_exit,
            environ={},
        ),
    )
    deps = WizardDeps(
        setup_service=lambda: service,
        persist_credentials=lambda *_a: tmp_path / "kairix.env",
        provider_names=lambda: ("azure_foundry", "openai"),
        index_poll_seconds=0.01,
    )

    # Interactive script: defaults through provider (azure_foundry pick,
    # endpoint + key typed so validation runs), skip neo4j, search
    # everything, and answer YES to "Start indexing now?".
    answers = iter(
        [
            "1",  # use case
            "1",  # provider: azure_foundry
            "https://res.services.ai.azure.com",  # endpoint
            "fake-key-for-tests",  # API key
            "",  # embed model default
            "",  # chat model default
            "1",  # storage: default
            "n",  # no knowledge graph
            "1",  # search everything
            "y",  # START INDEXING — the crash site
        ]
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers, ""))

    ctx = SetupContext(interactive=True, json_mode=False, state_path=tmp_path / ".state.json")
    with pytest.raises(SystemExit) as excinfo:
        setup_cli_main(
            ["--output", str(output), "--path", str(docs)],
            ctx=ctx,
            deps=deps,
        )
    # The ONLY SystemExit is the CLI's own clean exit — the index
    # worker's exit(3) was converted to an operator-facing status.
    assert excinfo.value.code == 0, f"setup crashed instead of finishing: exit {excinfo.value.code}"
    out = capsys.readouterr().out
    assert "another indexing run is already in progress" in out, f"lock guidance missing:\n{out}"
    assert "Setup complete" in out, f"setup did not reach the epilogue:\n{out}"
    assert output.exists()
