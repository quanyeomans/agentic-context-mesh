"""
E2E test: embed 50 real chunks via Azure, verify qmd vsearch returns correct docs.

Skipped unless QMD_E2E=1 is set. Requires:
  - Real QMD index at ~/.cache/qmd/index.sqlite
  - AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT set
  - qmd binary accessible

Run manually pre-deploy:
  QMD_E2E=1 python3 -m pytest tests/e2e/ -v -s
"""

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("QMD_E2E") != "1", reason="E2E tests skipped unless QMD_E2E=1")

QMD_BIN = Path("/data/workspace/.tools/qmd/node_modules/.bin/qmd")

# Known gold: (query, fragment that must appear in top-3 vsearch results)
GOLD_QUERIES = [
    ("Alex Jordan voice profile", "alex-jordan-voice-profile"),
    ("Arize Phoenix observability", "arize-observability-research"),
    ("SPF record duplicate permerror", "shared/rules"),
    ("QMD SQLite lock crash", "shared/facts"),
]


@pytest.fixture(scope="module")
def embedded_db(tmp_path_factory):
    """
    Copy the live QMD DB to a temp path, embed 50 chunks via Azure,
    and return the path. Restores env after.
    """
    from qmd_azure_embed.schema import get_qmd_db_path

    src = get_qmd_db_path()
    tmp_dir = tmp_path_factory.mktemp("qmd_e2e")
    tmp_db_path = tmp_dir / "index.sqlite"
    shutil.copy2(src, tmp_db_path)

    # Clear existing vectors in the copy
    db = sqlite3.connect(str(tmp_db_path))
    db.execute("DELETE FROM content_vectors")
    db.execute("DELETE FROM vectors_vec")
    db.commit()
    db.close()

    # Run embed on the copy with --limit 50
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("QMD_CACHE_DIR", str(tmp_dir))
        from qmd_azure_embed.embed import run_embed
        from qmd_azure_embed.schema import ensure_vec_table, validate_schema

        db = sqlite3.connect(str(tmp_db_path))
        validate_schema(db)
        ensure_vec_table(db)
        result = run_embed(db, force=False, limit=50)
        db.close()

    assert result["embedded"] > 0, f"No chunks embedded: {result}"
    return tmp_db_path


class TestVsearchQuality:
    def test_recall_queries_hit_gold(self, embedded_db, monkeypatch):
        """After embedding, vsearch should find known docs in top-3."""
        if not QMD_BIN.exists():
            pytest.skip("qmd binary not found")

        monkeypatch.setenv("QMD_CACHE_DIR", str(embedded_db.parent))

        passed = 0
        for query, gold_fragment in GOLD_QUERIES:
            result = subprocess.run(
                [str(QMD_BIN), "vsearch", query, "--json", "--limit", "3"],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "QMD_CACHE_DIR": str(embedded_db.parent)},
            )
            if result.returncode != 0:
                continue
            try:
                files = [r.get("file", "") for r in json.loads(result.stdout)[:3]]
                if any(gold_fragment.lower() in f.lower() for f in files):
                    passed += 1
            except (json.JSONDecodeError, TypeError):
                pass

        # At least 50% of gold queries must hit (conservative — only 50 chunks embedded)
        assert passed >= len(GOLD_QUERIES) // 2, (
            f"Only {passed}/{len(GOLD_QUERIES)} recall queries passed vsearch. Vector quality may be degraded."
        )

    def test_no_empty_results(self, embedded_db, monkeypatch):
        """Every query should return at least 1 result after embedding."""
        if not QMD_BIN.exists():
            pytest.skip("qmd binary not found")

        monkeypatch.setenv("QMD_CACHE_DIR", str(embedded_db.parent))

        result = subprocess.run(
            [str(QMD_BIN), "vsearch", "test query", "--json", "--limit", "3"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "QMD_CACHE_DIR": str(embedded_db.parent)},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) > 0, "vsearch returned zero results after embedding"
