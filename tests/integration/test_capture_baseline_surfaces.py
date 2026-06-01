"""Integration smoke test — ``scripts/cutover/capture_baseline.py`` captures
all four surfaces end-to-end against the current kairix CLI shape.

Resolves #377. The pre-#377 script shelled out to ``kairix benchmark
--concurrency 1 --json`` and ``kairix probe --suite`` — both surfaces
that no longer exist on the unified benchmark CLI — so on production
the script reported ``captured 0/4 surfaces``. This test pins the new
CLI shape:

  * eval     -> ``kairix benchmark run --suite <name> --output <dir>``
  * latency  -> ``kairix benchmark run --suite <name> --mode single-shot
                 --output <dir>``
  * sample-journey -> ``kairix search --json <query>``
  * state    -> SQLite roll-up (unchanged)

Boundary chain exercised end-to-end:

  create_schema + migrate (canonical schema)
    -> seed documents/content via the public SQL surface
      -> write kairix.config.yaml with paths.documents_db + cutover.sample_queries
        -> install fake ``kairix`` shim on PATH that emits the report
           shapes the unified CLI produces today
          -> subprocess(python3 scripts/cutover/capture_baseline.py ...)
            -> assert envelope has 4/4 surfaces with the projected fields
              (state.per_collection, eval.reflib.recall_at_10,
               latency.p95_ms, sample_journey[*].top_paths)

F47 — the DB seeding uses the canonical ``create_schema`` + ``migrate``
shape rather than rolling its own CREATE TABLE; the script-under-test
remains the production binary invoked via subprocess. F1/F2-clean — no
monkeypatching, no env-var mutation of the production process; the
shim binary lives entirely in ``tmp_path``.

Sabotage proof. Executed locally 2026-06-01:

  * Mutation: drop the ``recall_at_10`` projection from
    ``_project_eval_payload`` in ``scripts/cutover/capture_baseline.py``.
  * Observed failure (verbatim):
    ``AssertionError: eval.reflib.recall_at_10 must be the float
      surrogate (NDCG@10) but got payload={...} no key``
  * Restore: revert the projection deletion.
  * Re-run: PASS.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema, migrate

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _seed_documents_db(db_path: Path) -> None:
    """Create the canonical schema + insert three rows the state surface counts.

    Uses the canonical ``create_schema`` + ``migrate`` from
    ``kairix.core.db.schema`` so the schema matches production exactly —
    no hand-rolled CREATE TABLE that drifts when the production schema
    moves.
    """
    db = sqlite3.connect(str(db_path))
    try:
        create_schema(db)
        migrate(db)
        rows = [
            ("collection-alpha", "hash-aaa", "alpha body text"),
            ("collection-alpha", "hash-bbb", "alpha second body"),
            ("collection-beta", "hash-ccc", "beta short"),
        ]
        for collection, content_hash, body in rows:
            db.execute(
                "INSERT OR REPLACE INTO content (hash, doc) VALUES (?, ?)",
                (content_hash, body),
            )
            db.execute(
                "INSERT INTO documents "
                "(collection, path, hash, source_uri, source_modified_at, "
                "sensitivity, created_at, modified_at, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    collection,
                    f"docs/{content_hash}.md",
                    content_hash,
                    f"src://test/{content_hash}",
                    "2026-06-01T00:00:00Z",
                    "internal",
                    "2026-06-01T00:00:00Z",
                    "2026-06-01T00:00:00Z",
                ),
            )
        db.commit()
    finally:
        db.close()


def _install_kairix_shim(bin_dir: Path) -> Path:
    """Write a fake ``kairix`` script that emits the CLI shapes capture reads.

    The script handles two invocations:

      * ``kairix benchmark run --suite <s> [--mode <m>] --output <dir>``
        — writes ``<dir>/B-<suite>-mock-<date>.json`` carrying the
        ``summary`` block (ndcg_at_10/hit_rate_at_5/mrr_at_10/
        weighted_total) the projection reads, plus
        ``diagnostics.per_query_runs`` for the latency phase.
      * ``kairix search --json <query>`` — prints the search-envelope
        JSON the sample-journey surface parses (carries ``results``
        list with ``path`` keys).

    No real kairix installation is touched; the shim lives entirely
    under ``tmp_path``. Returns the path to the shim executable so the
    caller can prepend its parent to ``PATH``.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "kairix"
    shim.write_text(
        textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            """Fake kairix CLI for capture_baseline integration test."""
            from __future__ import annotations

            import json
            import sys
            from datetime import datetime, timezone
            from pathlib import Path


            def _today() -> str:
                return datetime.now(timezone.utc).strftime("%Y-%m-%d")


            def _emit_benchmark(args: list[str]) -> int:
                # Required: --suite NAME --output DIR
                suite = "unknown"
                output = None
                mode = "legacy"
                for i, a in enumerate(args):
                    if a == "--suite" and i + 1 < len(args):
                        suite = args[i + 1]
                    elif a == "--output" and i + 1 < len(args):
                        output = args[i + 1]
                    elif a == "--mode" and i + 1 < len(args):
                        mode = args[i + 1]
                if output is None:
                    print("missing --output", file=sys.stderr)
                    return 1
                out_dir = Path(output)
                out_dir.mkdir(parents=True, exist_ok=True)
                # Headline numbers vary slightly per suite so the
                # operator can verify the right suite ran.
                headline = {
                    "reflib": {"ndcg": 0.83, "hit5": 0.71, "mrr": 0.74, "total": 0.79},
                    "locomo": {"ndcg": 0.42, "hit5": 0.39, "mrr": 0.41, "total": 0.40},
                }.get(suite, {"ndcg": 0.5, "hit5": 0.5, "mrr": 0.5, "total": 0.5})
                # Per-query latency rows for the latency surface.
                per_query_runs = [
                    {"query_id": f"q-{i:02d}", "category": "recall",
                     "latency_ms": 10.0 + i * 2.5, "latency_phase":
                     ("cold" if i == 0 else "warm")}
                    for i in range(20)
                ]
                cases = [
                    {"id": f"q-{i:02d}", "category": "recall",
                     "elapsed_ms": 10.0 + i * 2.5, "score": 0.8}
                    for i in range(20)
                ]
                report = {
                    "meta": {
                        "suite_name": suite,
                        "system": "mock",
                        "date": _today(),
                        "mode": mode,
                        "weighted_total": headline["total"],
                    },
                    "summary": {
                        "ndcg_at_10": headline["ndcg"],
                        "hit_rate_at_5": headline["hit5"],
                        "mrr_at_10": headline["mrr"],
                        "weighted_total": headline["total"],
                        "category_scores": {"recall": headline["ndcg"]},
                        "gates": {},
                    },
                    "diagnostics": {
                        "category_counts": {"recall": 20},
                        "mode": mode,
                        "per_query_runs": per_query_runs,
                    },
                    "cases": cases,
                }
                report_path = out_dir / f"B-{suite}-mock-{_today()}.json"
                report_path.write_text(json.dumps(report, indent=2))
                print(f"Wrote {report_path}")
                return 0


            def _emit_search(args: list[str]) -> int:
                # ``kairix search --json <query>`` — emit a stable
                # envelope so the dedup digest is reproducible.
                query = args[-1] if args else "unknown"
                payload = {
                    "results": [
                        {"path": f"docs/{i}.md", "score": 0.9 - i * 0.1}
                        for i in range(5)
                    ],
                    "query": query,
                }
                print(json.dumps(payload))
                return 0


            def main() -> int:
                argv = sys.argv[1:]
                if not argv:
                    print("kairix shim: no subcommand", file=sys.stderr)
                    return 2
                sub = argv[0]
                if sub == "benchmark" and len(argv) >= 2 and argv[1] == "run":
                    return _emit_benchmark(argv[2:])
                if sub == "search":
                    return _emit_search(argv[1:])
                print(f"kairix shim: unhandled {argv!r}", file=sys.stderr)
                return 2


            if __name__ == "__main__":
                sys.exit(main())
            '''
        ),
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return shim


def _write_operator_config(config_path: Path, db_path: Path) -> None:
    """Write a minimal kairix.config.yaml with the two blocks capture reads."""
    config_path.write_text(
        textwrap.dedent(
            f"""\
            _schema_version: 1
            provider: openai
            paths:
              documents_db: {db_path}
            cutover:
              sample_queries:
                - "what is the cutover protocol"
                - "how does capture_baseline work"
            """
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Test surface
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Resolve the repo root from this test file's location.

    Avoids relying on cwd — the subprocess runs ``python3 scripts/...``
    against an absolute path so the test passes whether invoked from
    the worktree, the primary checkout, or a CI runner.
    """
    here = Path(__file__).resolve()
    # tests/integration/test_capture_baseline_surfaces.py -> repo root
    return here.parent.parent.parent


def test_capture_baseline_captures_all_four_surfaces(tmp_path: Path) -> None:
    """Outcome — capture_baseline.py emits the 4-surface envelope with the
    correct projection against the current kairix CLI shape."""
    db_path = tmp_path / "documents.sqlite"
    _seed_documents_db(db_path)

    config_path = tmp_path / "kairix.config.yaml"
    _write_operator_config(config_path, db_path)

    bin_dir = tmp_path / "bin"
    _install_kairix_shim(bin_dir)

    out_path = tmp_path / "baseline.json"
    repo_root = _repo_root()

    # Construct PATH that prepends the shim dir so subprocess resolves
    # our fake ``kairix`` before the real one (if installed). We do not
    # mutate the parent process's environment.
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"

    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "cutover" / "capture_baseline.py"),
            "--flag",
            "obsidian_connector_primary",
            "--out",
            str(out_path),
            "--config",
            str(config_path),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"capture_baseline failed: rc={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    # The script logs to stderr (INFO level via logging.basicConfig),
    # not stdout, so the "captured N/4" line appears in stdout.
    assert "/4 surfaces" in proc.stdout, proc.stdout

    envelope = json.loads(out_path.read_text())

    # ── flag + envelope shape ───────────────────────────────────────
    assert envelope["flag"] == "obsidian_connector_primary"
    assert "captured_at" in envelope
    assert "version" in envelope

    # ── state surface ────────────────────────────────────────────────
    state = envelope.get("state")
    assert state is not None, "state surface must populate from the seeded DB"
    by_collection = {row["collection"]: row for row in state["per_collection"]}
    assert by_collection["collection-alpha"]["doc_count"] == 2
    assert by_collection["collection-beta"]["doc_count"] == 1
    assert state["content_hash_digest"].startswith("sha256:")

    # ── eval surface — projected onto diff_baseline shape ────────────
    eval_block = envelope.get("eval")
    assert eval_block is not None, "eval surface must capture via the shim"
    reflib = eval_block.get("reflib")
    assert reflib is not None, f"reflib payload missing: eval_block={eval_block!r}"
    # recall_at_10 mirrors ndcg_at_10; diff_baseline's recall gate reads it.
    assert "recall_at_10" in reflib, (
        f"eval.reflib.recall_at_10 must be the float surrogate (NDCG@10) but got payload={reflib!r} no key"
    )
    assert reflib["recall_at_10"] == pytest.approx(0.83)
    assert reflib["ndcg_at_10"] == pytest.approx(0.83)
    assert reflib["hit_rate_at_5"] == pytest.approx(0.71)
    assert reflib["weighted_total"] == pytest.approx(0.79)
    # LoCoMo too — the shim emits a separate report when --suite locomo runs.
    locomo = eval_block.get("locomo")
    assert locomo is not None
    assert locomo["recall"] == pytest.approx(0.42)

    # ── latency surface ──────────────────────────────────────────────
    latency = envelope.get("latency")
    assert latency is not None, "latency surface must capture via single-shot mode"
    # The shim emits 20 per-query rows with latencies 10.0, 12.5, 15.0, ...
    # p50 ~ midpoint, p99 close to the tail.
    assert latency["p50_ms"] > 0
    assert latency["p95_ms"] >= latency["p50_ms"]
    assert latency["p99_ms"] >= latency["p95_ms"]

    # ── sample-journey surface ───────────────────────────────────────
    journey = envelope.get("sample_journey")
    assert journey is not None
    assert len(journey) == 2, f"expected 2 sample queries, got: {journey!r}"
    queries = [row["query"] for row in journey]
    assert "what is the cutover protocol" in queries
    assert "how does capture_baseline work" in queries
    for row in journey:
        assert row["top_paths"] == ["docs/0.md", "docs/1.md", "docs/2.md", "docs/3.md", "docs/4.md"]


def test_capture_baseline_reports_count_in_stdout(tmp_path: Path) -> None:
    """Smoke — the operator-facing ``captured N/4 surfaces`` line is non-empty
    even when only the state surface is requested.

    This pins #377's regression signal: ``captured 0/4 surfaces`` should
    NEVER appear when at least one surface was asked for and its inputs
    exist. The original bug was that the eval + latency surfaces hit
    deprecated CLI shapes and silently dropped to null — this assertion
    catches the same shape regression at the script's stdout summary
    line.
    """
    db_path = tmp_path / "documents.sqlite"
    _seed_documents_db(db_path)
    config_path = tmp_path / "kairix.config.yaml"
    _write_operator_config(config_path, db_path)
    out_path = tmp_path / "baseline.json"
    repo_root = _repo_root()
    # state-only surface — no need for the shim because eval/latency
    # are not invoked.
    proc = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts" / "cutover" / "capture_baseline.py"),
            "--flag",
            "some_flag",
            "--out",
            str(out_path),
            "--config",
            str(config_path),
            "--surface",
            "state",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    # The summary line counts only NOT-None surfaces; with state only,
    # we expect "captured 1/4 surfaces (state)".
    assert "captured 1/4 surfaces" in proc.stdout
    assert "state" in proc.stdout
