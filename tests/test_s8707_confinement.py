"""S8707 agentic-path-injection confinement sweep (PLA-279).

SonarCloud's ``pythonsecurity:S8707`` flags agent/operator-supplied CLI paths
that flow to a filesystem sink — an LLM driving the CLI with a crafted
``../../etc/passwd`` (or an absolute escape) could read/write off-tree. The fix
is the canonical kairix allow-list sanitiser: resolve the candidate and verify
it sits under a legitimate working-area root BEFORE any open(), raising
:class:`kairix.paths.PathTraversalError` otherwise.

This module pins the contract two ways:

  * the shared mechanism — :func:`kairix.paths.confine_to_roots` /
    :func:`kairix.paths.agent_cli_roots` — accepts an in-root path and rejects
    every escape shape (``..`` traversal, absolute-outside);
  * each confined SITE wires that mechanism, so an escaping path is rejected at
    the public surface. The "in-root accepted" half is covered by the mechanism
    test plus every site's existing happy-path suite (which passes ``tmp_path``
    paths and would break here if confinement wrongly rejected them); the
    highest-value sites (mechanism, ingest-chat) carry an explicit accept case.

F1/F2-clean: collaborators are injected via existing dataclass/Namespace seams
and canonical fakes — no monkeypatch, no env vars. Scratch files live under
``tmp_path`` only.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from kairix.paths import KairixPaths, PathTraversalError

pytestmark = pytest.mark.unit

# An absolute path that is outside every allow-list root (cwd / home / tempdir)
# on both macOS and Linux CI. ``confine_to_roots`` rejects it BEFORE any read.
_ABSOLUTE_ESCAPE = "/etc/passwd"


def _paths(tmp_path: Path) -> KairixPaths:
    """KairixPaths pinned to tmp_path — never reads env (F2)."""
    return KairixPaths(
        document_root=tmp_path / "vault",
        db_path=tmp_path / "kairix.db",
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )


# The shared mechanism (confine_to / confine_to_roots / agent_cli_roots) is
# exercised — and mutation-pinned — in tests/test_paths.py::TestPathConfinement
# (same-module as kairix/paths.py). This module proves each SITE wires it.


# ---------------------------------------------------------------------------
# Site: kairix.use_cases.ingest_chat.ingest_chat  (_read_turns, MAJOR)
# ---------------------------------------------------------------------------


def test_ingest_chat_rejects_transcript_path_escape(tmp_path: Path) -> None:
    """An out-of-root transcript path is rejected before _read_turns opens it.

    Sabotage: drop the ``confine_to_roots`` line at the top of ``ingest_chat`` →
    the absolute path is opened (FileNotFoundError / off-tree read) instead of
    raising PathTraversalError, and pytest.raises fails.
    """
    from tests.fakes import FakeFactExtractor, FakeFactStore

    with pytest.raises(PathTraversalError):
        ingest_chat_use_case(
            Path(_ABSOLUTE_ESCAPE),
            paths=_paths(tmp_path),
            fact_store=FakeFactStore(),
            fact_extractor=FakeFactExtractor(scripted_facts=[]),
            no_extract=True,
        )


def test_ingest_chat_accepts_in_root_transcript(tmp_path: Path) -> None:
    """A transcript under tmp_path (temp-dir root) is read and processed."""
    from tests.fakes import FakeFactExtractor, FakeFactStore

    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"role": "user", "content": "hi", "conversation_id": "c1"}) + "\n",
        encoding="utf-8",
    )
    result = ingest_chat_use_case(
        transcript,
        paths=_paths(tmp_path),
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(scripted_facts=[]),
        no_extract=True,
    )
    assert result.turns_ingested == 1


def ingest_chat_use_case(*args: Any, **kwargs: Any) -> Any:
    """Local import shim so the heavy use-case module is imported lazily."""
    from kairix.use_cases.ingest_chat import ingest_chat

    return ingest_chat(*args, **kwargs)


# ---------------------------------------------------------------------------
# Site: kairix.quality.probe.config_cli.main  (--output writes, --compare read)
# ---------------------------------------------------------------------------


class _StubSnapshotter:
    """Fixed transport snapshot so the probe never pokes real transport."""

    def snapshot(self) -> Any:
        from kairix.quality.probe.config_runner import TransportSnapshot

        return TransportSnapshot(coalesce_ratio=0.1, cache_hit_rate=0.5, pool_acquire_p50_ms=5.0)


def _registry_with(name: str = "openai") -> Any:
    from tests.fakes import FakeProvider, FakeProviderRegistry

    return FakeProviderRegistry({name: FakeProvider(name=name, vector=[0.1, 0.2, 0.3])})


def _short_argv(*extra: str) -> list[str]:
    return ["--warm-samples", "1", "--concurrency", "1", "--repeated-samples", "1", *extra]


def test_config_cli_rejects_output_path_escape(tmp_path: Path) -> None:
    """``--output`` outside the allow-list is rejected (exit 2) before any write.

    Sabotage: remove the ``_validate_output_path`` call in ``main`` → the probe
    runs and writes to the off-tree path (exit 0), so the exit-2 + message
    assertions fail.
    """
    from kairix.quality.probe.config_cli import main as config_main

    err = io.StringIO()
    with redirect_stderr(err):
        rc = config_main(
            _short_argv("--provider", "openai", "--output", "/etc/probe-escape.json"),
            registry=_registry_with(),
            snapshotter=_StubSnapshotter(),
            env_provider_lookup=lambda: None,
        )
    assert rc == 2
    assert "escapes the allowed roots" in err.getvalue()


def test_config_cli_accepts_in_root_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An in-root ``--output`` path is accepted and the report file is written."""
    from kairix.quality.probe.config_cli import main as config_main

    out_path = tmp_path / "report.json"
    rc = config_main(
        _short_argv("--provider", "openai", "--output", str(out_path)),
        registry=_registry_with(),
        snapshotter=_StubSnapshotter(),
        env_provider_lookup=lambda: None,
    )
    assert rc == 0
    assert out_path.exists()


def test_config_cli_rejects_compare_path_escape(tmp_path: Path) -> None:
    """``--compare`` outside the allow-list is rejected by _load_baseline.

    Sabotage: remove the ``confine_to_roots`` guard in ``_load_baseline`` → the
    escaping path falls through to the ``does not exist`` branch, so the
    "escapes the allowed roots" message assertion fails.
    """
    from kairix.quality.probe.config_cli import main as config_main

    err = io.StringIO()
    with redirect_stderr(err):
        rc = config_main(
            _short_argv("--provider", "openai", "--compare", "/etc/probe-baseline.json"),
            registry=_registry_with(),
            snapshotter=_StubSnapshotter(),
            env_provider_lookup=lambda: None,
        )
    assert rc == 2
    assert "escapes the allowed roots" in err.getvalue()


# ---------------------------------------------------------------------------
# Site: kairix.quality.probe.perf_runner.load_budgets
# ---------------------------------------------------------------------------


def test_load_budgets_rejects_path_escape() -> None:
    """An out-of-root budgets path is rejected before read.

    Sabotage: drop the ``confine_to_roots`` line in ``load_budgets`` → the
    absolute path is read (FileNotFoundError) instead of PathTraversalError.
    """
    from kairix.quality.probe.perf_runner import load_budgets

    with pytest.raises(PathTraversalError):
        load_budgets(Path(_ABSOLUTE_ESCAPE))


def test_load_budgets_accepts_in_root_path(tmp_path: Path) -> None:
    """A valid in-root budgets file loads."""
    from kairix.quality.probe.perf_runner import load_budgets

    budgets = tmp_path / "budgets.json"
    budgets.write_text(json.dumps({"search": {"p50_ms": 10.0, "p99_ms": 50.0}}), encoding="utf-8")
    loaded = load_budgets(budgets)
    assert loaded["search"]["p50_ms"] == 10.0


# ---------------------------------------------------------------------------
# Site: kairix.quality.benchmark.baseline.load_result
# ---------------------------------------------------------------------------


def test_load_result_rejects_path_escape() -> None:
    """An out-of-root benchmark-result path is rejected before read.

    Sabotage: drop the ``confine_to_roots`` line in ``load_result`` → the
    absolute path reaches the exists()/read branch instead of raising
    PathTraversalError.
    """
    from kairix.quality.benchmark.baseline import load_result

    with pytest.raises(PathTraversalError):
        load_result(_ABSOLUTE_ESCAPE)


def test_load_result_accepts_in_root_path(tmp_path: Path) -> None:
    """A valid in-root result file loads."""
    from kairix.quality.benchmark.baseline import load_result

    result = tmp_path / "bench.json"
    result.write_text(json.dumps({"summary": {"weighted_total": 0.8}}), encoding="utf-8")
    loaded = load_result(result)
    assert loaded["summary"]["weighted_total"] == 0.8


# ---------------------------------------------------------------------------
# Site: kairix.quality.eval.suite_runner.SuiteRunner.discover_suite
# ---------------------------------------------------------------------------


def _suite_runner(tmp_path: Path) -> Any:
    from kairix.quality.eval.suite_runner import SuiteRunner
    from tests.fakes import FakeFactExtractor, FakeFactStore, FakeLLMBackend

    return SuiteRunner(
        fact_store=FakeFactStore(),
        fact_extractor=FakeFactExtractor(scripted_facts=[]),
        llm=FakeLLMBackend(chat_response="1.0"),
        paths=_paths(tmp_path),
    )


def test_discover_suite_rejects_suite_path_escape(tmp_path: Path) -> None:
    """An out-of-root ``--suite`` directory is rejected before any glob/read.

    Sabotage: drop the ``confine_to_roots`` line at the top of
    ``discover_suite`` → an out-of-root dir like /etc raises the generic
    "No session-*.jsonl" ValueError (or "does not exist"), not
    PathTraversalError, so pytest.raises fails.
    """
    runner = _suite_runner(tmp_path)
    with pytest.raises(PathTraversalError):
        runner.discover_suite(Path("/etc"))


# ---------------------------------------------------------------------------
# Site: kairix.knowledge.entities.cli.cmd_suggest (--file read)
# ---------------------------------------------------------------------------


def test_cmd_suggest_rejects_file_path_escape() -> None:
    """``entity suggest --file`` outside the allow-list returns 1 before read.

    Sabotage: revert ``cmd_suggest`` to ``Path(args.file).read_text`` → the
    absolute path is read (OSError still returns 1, but no confinement message),
    so the ERROR-message assertion that names the allow-list fails.
    """
    from kairix.knowledge.entities.cli import cmd_suggest

    args = argparse.Namespace(text="", file=_ABSOLUTE_ESCAPE, format="text")
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = cmd_suggest(args)
    assert rc == 1
    assert "escapes the allowed roots" in err.getvalue()


# ---------------------------------------------------------------------------
# Site: kairix.knowledge.entities.cli.cmd_audit (--output write)
# ---------------------------------------------------------------------------


class _ScriptedNeo4j:
    """Minimal Neo4j seam: returns scripted rows, reports available."""

    def __init__(self, *, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = list(rows or [])
        self.available = True

    def cypher(self, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return list(self._rows)


def test_cmd_audit_rejects_output_path_escape() -> None:
    """``entity audit --output`` outside the allow-list returns 1 before write.

    Sabotage: revert ``cmd_audit`` to ``Path(args.output).write_text`` → the
    off-tree write succeeds (rc 0), so the rc==1 + message assertions fail.
    """
    from kairix.knowledge.entities.cli import build_parser, cmd_audit
    from kairix.use_cases.entity_audit import EntityAuditDeps

    args = build_parser().parse_args(
        ["audit", "--mode", "all", "--format", "json", "--output", "/etc/audit-escape.json"]
    )
    deps = EntityAuditDeps(neo4j_client=_ScriptedNeo4j(), now_fn=lambda: "T")
    err = io.StringIO()
    with redirect_stderr(err), redirect_stdout(io.StringIO()):
        rc = cmd_audit(args, deps=deps)
    assert rc == 1
    assert "escapes the allowed roots" in err.getvalue()


# ---------------------------------------------------------------------------
# Site: kairix.agents.curator.cli.main (health --output write)
# ---------------------------------------------------------------------------


def test_curator_health_rejects_output_path_escape() -> None:
    """``curator health --output`` outside the allow-list exits 1 before write.

    Sabotage: revert the write site to ``Path(args.output).write_text`` → the
    off-tree write succeeds and exits 0, so the SystemExit(1) + message
    assertions fail.
    """
    from kairix.agents.curator.cli import main as curator_main
    from tests.fixtures.neo4j_mock import FakeNeo4jClient

    err = io.StringIO()
    with pytest.raises(SystemExit) as excinfo, redirect_stderr(err), redirect_stdout(io.StringIO()):
        curator_main(
            ["health", "--output", "/etc/curator-escape.json", "--format", "text"],
            neo4j_client=FakeNeo4jClient(entities=[]),
        )
    assert excinfo.value.code == 1
    assert "escapes the allowed roots" in err.getvalue()


# ---------------------------------------------------------------------------
# Site: kairix.use_cases.remember.remember (false positive — agent allowlist)
# ---------------------------------------------------------------------------


def test_remember_path_injection_agent_is_rejected_no_escape_write(tmp_path: Path) -> None:
    """A path-traversal agent name is rejected by the upstream allowlist before
    the write surface is built — the guard that makes remember.py's S8707
    finding a false positive (sonar ignore: remember-cli-paths).

    The traversal target resolves under ``tmp_path`` (outside the vault but
    inside the test sandbox) so the sabotage run leaves no debris outside
    ``tmp_path``.

    Sabotage: remove the ``if agent not in allowed`` guard in ``remember`` →
    the traversal name flows into _resolve_write_dir, a file is written, and
    ``result.error``/``result.path``/"no escape" assertions fail.
    """
    from kairix.use_cases.remember import RememberDeps, remember

    document_root = tmp_path / "vault"
    escape_dir = tmp_path / "escaped-memory"  # sibling of the vault, under tmp_path
    injection_agent = "../../escaped-memory"  # ..(out of 04-Agent-Knowledge)../(out of vault)
    result = remember(
        injection_agent,
        "exfiltrate me",
        # db_path / index seams pinned to tmp_path so a guard-removed sabotage
        # run cannot touch the real DB (rejection normally fires before they run).
        deps=RememberDeps(
            config_fn=lambda: {},
            document_root_fn=lambda: document_root,
            db_path_fn=lambda: tmp_path / "db.sqlite",
            index_fn=lambda _db, _root, _target, _hash: False,
        ),
    )
    assert result.error.startswith("InvalidAgent:")
    assert result.path == ""
    assert not escape_dir.exists(), "no memory file may escape the document root"
