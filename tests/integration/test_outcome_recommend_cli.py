"""F30 outcome test — ``kairix recommend`` subprocess surface.

Drives the composed production path end-to-end:

  build_capability_corpus(seeded tmp sqlite)  # pre-seed the corpus
  subprocess([kairix, recommend, '<task>', --json, --db-path <seeded>])
    → kairix/use_cases/recommend.py:main
    → flag gate (recommender ON via the CWD kairix.config.yaml overlay)
    → run_recommend(...) over collections=["capabilities"], agent=None
    → JSON envelope on stdout

F2-clean: no ``KAIRIX_*`` env vars. The ``recommender`` flag is flipped
ON through a real ``kairix.config.yaml`` ``features:`` overlay in the
subprocess CWD (the production flag-resolution chain reads the working
directory's config), and the index db lands via the ``--db-path`` flag —
the same F30 seam ``kairix remember`` uses.

Sabotage anchor: short-circuit ``main`` before ``run_recommend`` (e.g.
return the disabled envelope unconditionally) → the recommendations list
is empty and the ``contradict`` assertion fails. Flipping the overlay flag
to false yields the disabled envelope on stdout → the ``error == ""``
assertion fails. Verified locally.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_CONFIG_YAML_FLAG_ON = """\
features:
  recommender: true
"""

_CONFIG_YAML_FLAG_OFF = """\
features:
  recommender: false
"""


def _seed_corpus(db_path: Path) -> None:
    """Build a one-capability ``capabilities`` corpus into a tmp sqlite (BM25-only)."""
    from kairix.core.db.schema import create_schema
    from kairix.knowledge.capabilities.builder import (
        CapabilityCatalogueBuilder,
        CapabilityCorpusDeps,
        build_capability_corpus,
    )

    def _caps() -> list[dict[str, object]]:
        return [
            {
                "name": "contradict",
                "mcp_tool": "contradict",
                "cli": "kairix contradict",
                "category": "synthesis",
                "when_to_use": "Check new content against existing knowledge for conflicts.",
            },
        ]

    db = sqlite3.connect(db_path)
    try:
        create_schema(db)
        deps = CapabilityCorpusDeps(
            builder=CapabilityCatalogueBuilder(catalogue_fn=_caps, now_fn=lambda: "2026-06-20T00:00:00+00:00"),
            embed_batch_fn=lambda texts: [],  # BM25-only
        )
        result = build_capability_corpus(db, deps=deps)
        db.commit()
    finally:
        db.close()
    assert result.written == 1, f"corpus seed must write the capability: {result}"


def _run_recommend(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "kairix.cli", "recommend", *args],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
    )


def test_recommend_cli_ranks_seeded_capability(tmp_path: Path) -> None:
    """A seeded capability surfaces in the ranked JSON envelope, flag ON."""
    (tmp_path / "kairix.config.yaml").write_text(_CONFIG_YAML_FLAG_ON, encoding="utf-8")
    db_path = tmp_path / "index.sqlite"
    _seed_corpus(db_path)

    proc = _run_recommend(
        [
            "check content for conflicts",
            "--json",
            "--db-path",
            str(db_path),
        ],
        cwd=tmp_path,
    )

    assert proc.returncode == 0, f"recommend exited {proc.returncode}\nstderr: {proc.stderr}\nstdout: {proc.stdout}"
    envelope = json.loads(proc.stdout)
    assert envelope["error"] == ""
    names = [r["name"] for r in envelope["recommendations"]]
    assert "contradict" in names, f"expected the seeded capability ranked; got {names!r}"
    rec = next(r for r in envelope["recommendations"] if r["name"] == "contradict")
    # The kairix-tool recommendation carries a ready-to-call invocation.
    assert rec["cli"] == "kairix contradict"
    assert rec["mcp_tool"] == "contradict"


def test_recommend_cli_disabled_when_flag_off(tmp_path: Path) -> None:
    """Flag OFF — exit 1 with the disabled message on stderr; no recs."""
    (tmp_path / "kairix.config.yaml").write_text(_CONFIG_YAML_FLAG_OFF, encoding="utf-8")
    db_path = tmp_path / "index.sqlite"
    _seed_corpus(db_path)

    proc = _run_recommend(
        [
            "check content for conflicts",
            "--json",
            "--db-path",
            str(db_path),
        ],
        cwd=tmp_path,
    )

    assert proc.returncode == 1, f"expected exit 1 when disabled; stdout={proc.stdout!r}"
    assert "recommender is disabled" in proc.stderr
    envelope = json.loads(proc.stdout)
    assert envelope["recommendations"] == []
