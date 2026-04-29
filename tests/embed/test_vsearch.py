"""
E2E test: embed 50 real chunks via Azure, verify vector search returns correct docs.

Skipped unless KAIRIX_E2E=1 is set. Requires:
  - Real kairix index at ~/.cache/kairix/index.sqlite
  - KAIRIX_LLM_API_KEY + KAIRIX_LLM_ENDPOINT set

Run manually pre-deploy:
  KAIRIX_E2E=1 python3 -m pytest tests/e2e/ -v -s
"""

import os
import shutil
import sqlite3

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("KAIRIX_E2E") != "1", reason="E2E tests skipped unless KAIRIX_E2E=1")

_DATA_DIR = os.environ.get("KAIRIX_DATA_DIR", "/data")

# Known gold: (query, fragment that must appear in top-3 vsearch results)
GOLD_QUERIES = [
    ("Jordan Blake voice profile", "jordan-blake-voice-profile"),
    ("Arize Phoenix observability", "arize-observability-research"),
    ("SPF record duplicate permerror", "shared/rules"),
    ("SQLite lock crash", "shared/facts"),
]


@pytest.fixture(scope="module")
def embedded_db(tmp_path_factory):
    """
    Copy the live kairix DB to a temp path, embed 50 chunks via Azure,
    and return the path. Restores env after.
    """
    from kairix.core.db import get_db_path

    src = get_db_path()
    tmp_dir = tmp_path_factory.mktemp("kairix_e2e")
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
        mp.setenv("KAIRIX_DB_PATH", str(tmp_db_path))
        from kairix.core.embed.embed import run_embed
        from kairix.core.embed.schema import ensure_vec_table, validate_schema

        db = sqlite3.connect(str(tmp_db_path))
        validate_schema(db)
        ensure_vec_table(db)
        result = run_embed(db, force=False, limit=50)
        db.close()

    assert result["embedded"] > 0, f"No chunks embedded: {result}"
    return tmp_db_path


@pytest.mark.unit
class TestVsearchQuality:
    @pytest.mark.unit
    def test_recall_queries_hit_gold(self, embedded_db, monkeypatch):
        """After embedding, vector search should find known docs in top-3."""
        monkeypatch.setenv("KAIRIX_DB_PATH", str(embedded_db))

        from kairix.core.search.vector import vector_search

        passed = 0
        for query, gold_fragment in GOLD_QUERIES:
            try:
                results = vector_search(query, limit=3)
                files = [r.get("file", "") for r in results[:3]]
                if any(gold_fragment.lower() in f.lower() for f in files):
                    passed += 1
            except Exception:
                pass

        # At least 50% of gold queries must hit (conservative — only 50 chunks embedded)
        assert passed >= len(GOLD_QUERIES) // 2, (
            f"Only {passed}/{len(GOLD_QUERIES)} recall queries passed vector search. Vector quality may be degraded."
        )

    @pytest.mark.unit
    def test_no_empty_results(self, embedded_db, monkeypatch):
        """Every query should return at least 1 result after embedding."""
        monkeypatch.setenv("KAIRIX_DB_PATH", str(embedded_db))

        from kairix.core.search.vector import vector_search

        results = vector_search("test query", limit=3)
        assert len(results) > 0, "vector search returned zero results after embedding"
