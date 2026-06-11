"""F30 outcome test — ``kairix remember`` subprocess surface (#472).

Drives the composed production path end-to-end:

  subprocess([kairix, remember, agent-alpha, '<content>', --json,
              --document-root <tmp>, --db-path <tmp>])
    → kairix/use_cases/remember.py:main
    → remember(...) — config allowlist → classify → write → scan+FTS
    → JSON envelope on stdout

F2-clean: no ``KAIRIX_*`` env vars. The config carrying the ``agents:``
block is a real ``kairix.config.yaml`` in the subprocess CWD (the
production resolution chain reads the working directory), and the
document root / db path land via the ``--document-root`` / ``--db-path``
flags — the same F30 seams ``kairix bootstrap`` and ``kairix embed`` use.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_CONFIG_YAML = """\
agents:
  agent-alpha:
    harness: claude-code
    surfaces:
      - path: 04-Agent-Knowledge/agent-alpha
        label: memory
"""


def _run_remember(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", "remember", *args],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=cwd,
    )


def test_remember_cli_writes_memory_for_configured_agent_and_indexes_it(tmp_path: Path) -> None:
    """A configured external agent saves a memory; the JSON envelope names
    the written file, the file exists with the content, and a BM25 MATCH
    over the tmp index finds it immediately — the #472 acceptance path.

    Sabotage anchor: short-circuit ``remember`` before the write (or skip
    the index step) → the file-exists / FTS-row assertions fail.
    """
    (tmp_path / "kairix.config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")
    vault = tmp_path / "vault"
    db_path = tmp_path / "index.sqlite"

    proc = _run_remember(
        [
            "agent-alpha",
            "decided: adopt the kestrel rollout checklist for releases",
            "--kind",
            "decision",
            "--json",
            "--document-root",
            str(vault),
            "--db-path",
            str(db_path),
        ],
        cwd=tmp_path,
    )

    assert proc.returncode == 0, f"remember exited {proc.returncode}\nstderr: {proc.stderr}\nstdout: {proc.stdout}"

    envelope = json.loads(proc.stdout)
    assert envelope["error"] == ""
    assert envelope["agent"] == "agent-alpha"
    assert envelope["kind"] == "decision"
    assert envelope["classified_as"] == "semantic-decision"
    assert envelope["indexed"] is True, f"expected immediate indexing: {envelope}"

    written = Path(envelope["path"])
    assert written.exists(), f"expected memory file at {written}"
    assert written.parent == vault / "04-Agent-Knowledge" / "agent-alpha"
    assert "kestrel rollout checklist" in written.read_text(encoding="utf-8")

    db = sqlite3.connect(str(db_path))
    try:
        row = db.execute(
            "SELECT filepath FROM documents_fts WHERE documents_fts MATCH ? LIMIT 1",
            ("kestrel",),
        ).fetchone()
    finally:
        db.close()
    assert row is not None, "BM25 must find the remembered content immediately, not at the next worker tick"


def test_remember_cli_human_output_names_the_file_and_index_state(tmp_path: Path) -> None:
    """The default (non-JSON) output is a human-readable confirmation that
    names the written path and the indexed state.

    Sabotage anchor: drop the ``_format_human`` write → stdout is empty
    and the content assertions fail.
    """
    (tmp_path / "kairix.config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")

    proc = _run_remember(
        [
            "agent-alpha",
            "rule: always check the board before starting work",
            "--document-root",
            str(tmp_path / "vault"),
            "--db-path",
            str(tmp_path / "index.sqlite"),
        ],
        cwd=tmp_path,
    )

    assert proc.returncode == 0, f"remember exited {proc.returncode}\nstderr: {proc.stderr}"
    assert "Remembered for agent-alpha" in proc.stdout
    assert "indexed:" in proc.stdout
    assert "classified as: procedural-rule" in proc.stdout


def test_remember_cli_rejects_unconfigured_agent_with_f21_stderr(tmp_path: Path) -> None:
    """An agent missing from the config (and the legacy set) exits 1 with
    stderr that names the agent and carries the F21 fix:/next: affordance.
    No file is written.

    Sabotage anchor: remove the allowlist check from ``remember`` → exit
    code 0 and a file appears under the vault, failing both assertions.
    """
    (tmp_path / "kairix.config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")
    vault = tmp_path / "vault"

    proc = _run_remember(
        [
            "agent-omega",
            "anything",
            "--document-root",
            str(vault),
            "--db-path",
            str(tmp_path / "index.sqlite"),
        ],
        cwd=tmp_path,
    )

    assert proc.returncode == 1, f"expected exit 1 for unconfigured agent; stderr={proc.stderr!r}"
    assert "agent-omega" in proc.stderr
    assert "fix: add the agent to the agents: block in kairix.config.yaml" in proc.stderr
    assert "next: re-run kairix doctor agent --all" in proc.stderr
    assert not vault.exists(), "no memory file may be written for a rejected agent"
