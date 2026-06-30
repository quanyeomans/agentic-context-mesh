"""Unit tests for the remember use case (#472) — ``kairix/use_cases/remember.py``.

Pins the contract both surfaces (CLI ``kairix remember`` + MCP
``memory_write``) rely on:

  - a configured agent's memory lands as a dated markdown file under the
    agent's write surface, beneath the document root;
  - legacy agents keep working with no config (default-safe);
  - unconfigured agents are rejected with the F21 affordance and no file
    is written;
  - the file is pushed through the canonical document-scan + FTS-rebuild
    step so BM25 finds it immediately (``indexed`` reports the truth);
  - failures (bad kind, empty content, write errors, index errors) come
    back as structured envelopes — :func:`remember` never raises.

F1-clean: every collaborator is injected through ``RememberDeps``.
F2-clean: no env vars — config / roots / clock / db path are explicit.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from kairix.use_cases.remember import RememberDeps, remember
from kairix.use_cases.remember import main as remember_main

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 6, 11, 9, 30, tzinfo=timezone.utc)


class _RecordingClassifier:
    """Classifier stub honouring the ``(content, *, agent, config)`` contract."""

    def __init__(self, type_name: str = "semantic-decision", raises: BaseException | None = None) -> None:
        self.type_name = type_name
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def __call__(self, content: str, *, agent: str, config: dict[str, object] | None) -> Any:
        self.calls.append({"content": content, "agent": agent, "config": config})
        if self.raises is not None:
            raise self.raises

        class _Result:
            type = self.type_name

        return _Result()


class _RecordingIndexer:
    """Index seam stub recording its args and returning a scripted outcome."""

    def __init__(self, result: bool = True, raises: BaseException | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[Path, Path, Path, str]] = []

    def __call__(self, db_path: Path, document_root: Path, target: Path, content_hash: str) -> bool:
        self.calls.append((db_path, document_root, target, content_hash))
        if self.raises is not None:
            raise self.raises
        return self.result


def _scope_config(name: str, surface_path: str) -> dict[str, object]:
    return {
        "agents": {
            name: {
                "harness": "claude-code",
                "surfaces": [{"path": surface_path, "label": "memory"}],
            }
        }
    }


def _deps(
    tmp_path: Path,
    *,
    config: dict[str, object] | None = None,
    classify: Any | None = None,
    index: Any | None = None,
) -> RememberDeps:
    cfg = config if config is not None else {}
    return RememberDeps(
        config_fn=lambda: cfg,
        document_root_fn=lambda: tmp_path / "vault",
        db_path_fn=lambda: tmp_path / "index.sqlite",
        now_fn=lambda: _FIXED_NOW,
        classify_fn=classify if classify is not None else _RecordingClassifier(),
        index_fn=index if index is not None else _RecordingIndexer(),
    )


def test_configured_agent_memory_is_written_under_its_write_surface(tmp_path: Path) -> None:
    """Happy path: configured agent, relative surface path → dated file
    beneath the document root, envelope reports every field.

    Sabotage: skip the ``target.write_text`` call in ``remember`` → the
    ``Path.exists()`` assertion fails.
    """
    indexer = _RecordingIndexer(result=True)
    config = _scope_config("agent-alpha", "04-Agent-Knowledge/agent-alpha")
    result = remember(
        "agent-alpha",
        "decided: adopt the new release checklist",
        kind="decision",
        deps=_deps(tmp_path, config=config, index=indexer),
    )

    assert result.error == ""
    assert result.agent == "agent-alpha"
    assert result.kind == "decision"
    assert result.classified_as == "semantic-decision"
    assert result.indexed is True

    written = Path(result.path)
    assert written.exists(), f"expected memory file at {written}"
    assert written.parent == tmp_path / "vault" / "04-Agent-Knowledge" / "agent-alpha"
    assert written.name == "2026-06-11-decided-adopt-the-new-release-checklist.md"
    text = written.read_text(encoding="utf-8")
    assert "decided: adopt the new release checklist" in text
    assert "agent: agent-alpha" in text


def test_legacy_agent_with_no_config_uses_conventional_layout(tmp_path: Path) -> None:
    """Default-safe: ``builder`` works with an empty config and lands in
    ``04-Agent-Knowledge/builder``.

    Sabotage: drop the legacy-set union from the allowlist → this returns
    an InvalidAgent error and the assertion fails.
    """
    result = remember("builder", "rule: never skip the gate", deps=_deps(tmp_path))

    assert result.error == ""
    assert Path(result.path).parent == tmp_path / "vault" / "04-Agent-Knowledge" / "builder"


def test_unconfigured_agent_is_rejected_with_f21_affordance_and_no_file(tmp_path: Path) -> None:
    """Unknown agent → InvalidAgent envelope with fix:/next: markers; the
    vault is untouched.

    Sabotage: remove the allowlist check from ``remember`` → a file gets
    written and both assertions fail.
    """
    result = remember(
        "agent-omega",
        "anything",
        deps=_deps(tmp_path, config=_scope_config("agent-alpha", "04-Agent-Knowledge/agent-alpha")),
    )

    assert result.error.startswith("InvalidAgent:")
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in result.error
    assert "next: re-run kairix doctor agent --all" in result.error
    assert result.path == ""
    assert not (tmp_path / "vault").exists(), "no file may be written for a rejected agent"


def test_invalid_kind_is_rejected_with_actionable_error(tmp_path: Path) -> None:
    """A kind outside note|decision|fact is rejected before any I/O.

    Sabotage: remove the ``kind not in VALID_KINDS`` guard → the call
    succeeds and the error assertion fails.
    """
    result = remember("builder", "some text", kind="poem", deps=_deps(tmp_path))

    assert result.error.startswith("InvalidKind:")
    assert "fix:" in result.error
    assert result.path == ""


def test_empty_content_is_rejected(tmp_path: Path) -> None:
    """Whitespace-only content is rejected before any I/O.

    Sabotage: remove the empty-content guard → a file with empty body is
    written and the error assertion fails.
    """
    result = remember("builder", "   ", deps=_deps(tmp_path))

    assert result.error.startswith("EmptyContent:")
    assert "fix:" in result.error
    assert result.path == ""


def test_filename_collision_appends_counter(tmp_path: Path) -> None:
    """A second memory with the same date + slug gets a ``-2`` suffix
    instead of overwriting the first.

    Sabotage: drop the collision loop in ``_build_target_path`` → both
    calls return the same path and the distinct-path assertion fails.
    """
    deps = _deps(tmp_path)
    first = remember("builder", "rule: never skip the gate", deps=deps)
    second = remember("builder", "rule: never skip the gate", deps=deps)

    assert first.error == "" and second.error == ""
    assert first.path != second.path
    assert Path(second.path).name == "2026-06-11-rule-never-skip-the-gate-2.md"
    assert Path(first.path).exists() and Path(second.path).exists()


def test_index_seam_receives_db_path_root_target_and_content_hash(tmp_path: Path) -> None:
    """The injected index step gets the configured db path, the document
    root, the path of the file just written, and the hash of its body.

    The ``target`` is what lets the index step touch ONLY the new file
    instead of re-scanning the whole tree (PLA-258).

    Sabotage: stop calling ``d.index_fn`` in ``remember`` → the calls
    list stays empty and ``indexed`` flips to False.
    """
    indexer = _RecordingIndexer(result=True)
    result = remember("builder", "version: 2026.6.11 of the deploy stack", deps=_deps(tmp_path, index=indexer))

    assert result.indexed is True
    assert len(indexer.calls) == 1
    db_path, droot, target, content_hash = indexer.calls[0]
    assert db_path == tmp_path / "index.sqlite"
    assert droot == tmp_path / "vault"
    assert target == Path(result.path)
    from kairix.knowledge.reflib.dedup import hash_content

    assert content_hash == hash_content(Path(result.path).read_text(encoding="utf-8"))


def test_index_failure_still_saves_file_and_reports_not_indexed(tmp_path: Path) -> None:
    """An indexing error must not lose the memory: the file stays on disk,
    ``indexed`` is False, and the detail carries the re-index affordance.

    Sabotage: remove the try/except around the ``index_fn`` call → the
    OSError propagates and this test fails with an unhandled exception.
    """
    indexer = _RecordingIndexer(raises=OSError("db locked"))
    result = remember("builder", "rule: never skip the gate", deps=_deps(tmp_path, index=indexer))

    assert result.error == ""
    assert result.indexed is False
    assert "next: run kairix embed" in result.detail
    assert Path(result.path).exists()


def test_classifier_failure_is_advisory_not_fatal(tmp_path: Path) -> None:
    """A classifier blow-up downgrades to ``classified_as=unknown`` — the
    write still happens.

    Sabotage: remove the try/except in ``_classify_advisory`` → the
    ValueError propagates and this test fails with an unhandled exception.
    """
    classifier = _RecordingClassifier(raises=ValueError("classifier exploded"))
    result = remember("builder", "some plain text", deps=_deps(tmp_path, classify=classifier))

    assert result.error == ""
    assert result.classified_as == "unknown"
    assert Path(result.path).exists()


def test_write_failure_returns_writefailed_envelope(tmp_path: Path) -> None:
    """An OSError writing the file surfaces as WriteFailed with F21 guidance.

    The write dir is forced to collide with an existing FILE so ``mkdir``
    raises (NotADirectoryError ⊂ OSError).

    Sabotage: remove the try/except around the write → the OSError
    propagates and this test fails with an unhandled exception.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "04-Agent-Knowledge").write_text("blocking-file", encoding="utf-8")

    result = remember("builder", "rule: never skip the gate", deps=_deps(tmp_path))

    assert result.error.startswith("WriteFailed:")
    assert "fix:" in result.error and "next:" in result.error
    assert result.path == ""


def test_out_of_root_write_surface_is_rejected_with_actionable_error(tmp_path: Path) -> None:
    """A configured memory surface resolving OUTSIDE the document root is
    rejected BEFORE any write. The scanner only walks the document root, so a
    memory saved outside it would be permanently unsearchable — the envelope
    is an F21 MemoryUnreachable message naming both paths, and NO file is
    written (PLA-259).

    Sabotage-proof (executed): removed the ``_in_root_error`` guard call from
    ``remember`` → a file was written under the outside dir and both the
    error-prefix and no-file assertions failed; restored.
    """
    outside = tmp_path / "outside-the-store" / "mem"
    config = _scope_config("agent-alpha", str(outside))  # absolute path OUTSIDE the vault
    result = remember(
        "agent-alpha",
        "decided: adopt the new release checklist",
        kind="decision",
        deps=_deps(tmp_path, config=config),
    )

    assert result.error.startswith("MemoryUnreachable:")
    assert str(outside) in result.error  # names the escaping surface
    assert str(tmp_path / "vault") in result.error  # names the scanned root
    assert "fix:" in result.error and "next:" in result.error
    assert result.path == ""
    assert not outside.exists(), "no memory file may be written outside the scanned root"


def test_write_failed_names_path_permission_and_fix(tmp_path: Path) -> None:
    """A read-only / wrong-owned memory surface yields an F21 WriteFailed that
    names WHICH path, WHICH permission, and HOW to fix — not an opaque OSError
    (PLA-259). Simulated with a 0o500 surface dir; skips on hosts where mode
    bits do not block writes (e.g. CI run as root — the /run/secrets lesson).

    Sabotage-proof (executed): reverted ``_write_failed_error`` to the bare
    ``type(exc).__name__`` message → the permission/errno + fix-hint
    assertions failed; restored.
    """
    if os.geteuid() == 0:
        pytest.skip("permission denial cannot be simulated as root (write succeeds despite 0o500)")
    vault = tmp_path / "vault"
    write_dir = vault / "04-Agent-Knowledge" / "builder"
    write_dir.mkdir(parents=True)
    write_dir.chmod(0o500)  # r-x: the agent's memory dir exists but is not writable
    try:
        result = remember("builder", "rule: never skip the gate", deps=_deps(tmp_path))
        if result.error == "":
            pytest.skip("filesystem ignores mode bits (write succeeded despite 0o500)")
        assert result.error.startswith("WriteFailed:")
        assert str(write_dir) in result.error  # which path
        # which permission — both the symbolic errno (errno.errorcode mapping)
        # and the OS strerror are named, pinning both fallback expressions.
        assert "[EACCES]" in result.error
        assert "Permission denied" in result.error
        assert "fix:" in result.error and "next:" in result.error  # how to fix
        assert result.path == ""
    finally:
        write_dir.chmod(0o700)


def test_production_index_seam_makes_memory_bm25_searchable(tmp_path: Path) -> None:
    """The default index seam (scan + FTS rebuild) really makes the new
    memory findable: after ``remember``, a BM25 MATCH query over the tmp
    index returns the document. This is the "findable now, not at the
    next worker tick" outcome (#472).

    Hermetic: sqlite + filesystem only — the index step never calls the
    embedding provider.

    Sabotage: make the default ``index_fn`` skip ``default_index_file``
    → ``indexed`` is False and the FTS MATCH returns no rows.
    """
    deps = RememberDeps(
        config_fn=lambda: {},
        document_root_fn=lambda: tmp_path / "vault",
        db_path_fn=lambda: tmp_path / "index.sqlite",
        now_fn=lambda: _FIXED_NOW,
        classify_fn=_RecordingClassifier(),
        # index_fn left at the production default on purpose.
    )
    result = remember("builder", "decided: the quarterly osprey migration plan is approved", deps=deps)

    assert result.error == ""
    assert result.indexed is True, f"expected immediate indexing; detail={result.detail!r}"

    db = sqlite3.connect(str(tmp_path / "index.sqlite"))
    try:
        row = db.execute(
            "SELECT filepath FROM documents_fts WHERE documents_fts MATCH ? LIMIT 1",
            ("osprey",),
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "BM25 FTS must find the freshly remembered content"


def test_remember_indexes_only_the_new_file_not_the_whole_tree(tmp_path: Path) -> None:
    """A single ``remember()`` indexes ONLY the file it just wrote — it
    does NOT re-read and re-hash the rest of the vault (PLA-258).

    Seeds a multi-file corpus under the document root, then remembers one
    memory through the PRODUCTION index seam. The new memory is searchable
    now, AND the index holds exactly that one document — none of the
    pre-existing seeded files are pulled in. The old full-tree rescan
    would have ingested every seeded file (O(corpus) latency on the most
    latency-sensitive agent path); the incremental single-file index does
    not touch them.

    Hermetic: sqlite + filesystem only — no embedding provider.

    Sabotage: revert ``_index_single_file`` to the full-tree
    ``default_scan_documents`` → every seeded file lands in ``documents``
    and the ``doc_count == 1`` / ``seeded_in_index == 0`` assertions fail.
    """
    vault = tmp_path / "vault"
    seeded = vault / "02-Areas"
    seeded.mkdir(parents=True)
    for i in range(25):
        (seeded / f"seeded-{i}.md").write_text(f"# Seeded {i}\n\nprior vault content number {i}\n", encoding="utf-8")

    deps = RememberDeps(
        config_fn=lambda: {},
        document_root_fn=lambda: vault,
        db_path_fn=lambda: tmp_path / "index.sqlite",
        now_fn=lambda: _FIXED_NOW,
        classify_fn=_RecordingClassifier(),
        # index_fn left at the production default on purpose.
    )
    result = remember("builder", "decided: the quarterly osprey migration plan is approved", deps=deps)

    assert result.error == ""
    assert result.indexed is True, f"expected immediate indexing; detail={result.detail!r}"

    db = sqlite3.connect(str(tmp_path / "index.sqlite"))
    try:
        active_docs = db.execute("SELECT COUNT(*) FROM documents WHERE active = 1").fetchone()[0]
        seeded_in_index = db.execute(
            "SELECT COUNT(*) FROM documents WHERE path LIKE '02-Areas/%' AND active = 1"
        ).fetchone()[0]
        fts_hit = db.execute(
            "SELECT 1 FROM documents_fts WHERE documents_fts MATCH ? LIMIT 1",
            ("osprey",),
        ).fetchone()
    finally:
        db.close()

    assert fts_hit is not None, "the new memory must be BM25-searchable now"
    assert active_docs == 1, "only the remembered file may be indexed — the tree must not be rescanned"
    assert seeded_in_index == 0, "no pre-existing seeded vault file may be pulled into the index"


def test_list_schema_agent_falls_back_to_conventional_write_dir(tmp_path: Path) -> None:
    """A legacy LIST-shaped ``agents:`` block contributes the name to the
    allowlist, and the write surface falls back to the conventional
    ``04-Agent-Knowledge/<agent>`` layout (the scope loader only parses
    the mapping shape).

    Sabotage: remove the try/except in ``_resolve_write_dir`` → the
    ValueError from the scope loader propagates and this test fails with
    an unhandled exception.
    """
    config: dict[str, object] = {"agents": [{"name": "agent-beta", "write_path": "04-Agent-Knowledge/agent-beta"}]}
    result = remember("agent-beta", "rule: never overwrite a memory", deps=_deps(tmp_path, config=config))

    assert result.error == ""
    assert Path(result.path).parent == tmp_path / "vault" / "04-Agent-Knowledge" / "agent-beta"


# ---------------------------------------------------------------------------
# CLI adapter (main) — thin surface over the same use case
# ---------------------------------------------------------------------------


def _run_cli(args: list[str], deps: RememberDeps | None) -> tuple[str, str, int]:
    out, err = io.StringIO(), io.StringIO()
    code = remember_main(args, out=out, err=err, deps=deps)
    return out.getvalue(), err.getvalue(), code


def test_cli_human_output_reports_path_and_searchable_state(tmp_path: Path) -> None:
    """Default (non-JSON) output names the agent, the file, and the
    indexed state in plain language.

    Sabotage: drop the ``_format_human`` write from ``main`` → stdout is
    empty and the content assertions fail.
    """
    stdout, _stderr, code = _run_cli(["builder", "rule: never skip the gate"], _deps(tmp_path))

    assert code == 0
    assert "Remembered for builder" in stdout
    assert "searchable now" in stdout


def test_cli_human_output_carries_reindex_affordance_when_not_indexed(tmp_path: Path) -> None:
    """When indexing didn't happen, the human line carries the
    ``next: run kairix embed`` affordance.

    Sabotage: drop the not-indexed branch from ``_format_human`` → the
    affordance assertion fails.
    """
    stdout, _stderr, code = _run_cli(
        ["builder", "rule: never skip the gate"],
        _deps(tmp_path, index=_RecordingIndexer(result=False)),
    )

    assert code == 0
    assert "next: run kairix embed" in stdout


def test_cli_error_path_writes_stderr_and_exits_1(tmp_path: Path) -> None:
    """A rejected agent maps to exit 1 with the F21 message on stderr.

    Sabotage: make ``main`` return 0 regardless of ``result.error`` →
    the exit-code assertion fails.
    """
    _stdout, stderr, code = _run_cli(
        ["agent-omega", "anything"],
        _deps(tmp_path, config=_scope_config("agent-alpha", "04-Agent-Knowledge/agent-alpha")),
    )

    assert code == 1
    assert "agent-omega" in stderr
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in stderr


def test_cli_flags_build_override_deps_for_subprocess_seam(tmp_path: Path) -> None:
    """``--document-root`` / ``--db-path`` flags construct the override
    deps (the F30 subprocess seam) when no in-process deps are passed —
    the write and the index land under the tmp paths, never the real ones.

    Sabotage: drop the flag handling from ``_deps_from_args`` → the file
    lands relative to the production document root and the parent-dir
    assertion fails.
    """
    stdout, _stderr, code = _run_cli(
        [
            "builder",
            "rule: never skip the gate",
            "--json",
            "--document-root",
            str(tmp_path / "vault"),
            "--db-path",
            str(tmp_path / "index.sqlite"),
        ],
        None,
    )

    assert code == 0
    envelope = json.loads(stdout)
    assert Path(envelope["path"]).parent == tmp_path / "vault" / "04-Agent-Knowledge" / "builder"
    assert envelope["indexed"] is True
    assert (tmp_path / "index.sqlite").exists()


def test_cli_without_flags_or_deps_rejects_empty_content_before_any_write(tmp_path: Path) -> None:
    """With neither flags nor injected deps, ``main`` resolves production
    deps — the empty-content guard must fire before any filesystem write
    so this path stays hermetic.

    Sabotage: reorder ``remember`` to resolve paths before the content
    guard → this test would touch the real document root.
    """
    _stdout, stderr, code = _run_cli(["builder", ""], None)

    assert code == 1
    assert "EmptyContent" in stderr
