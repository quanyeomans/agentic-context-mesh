"""Capture pre-flip / post-flip baseline state for a feature-flag cutover.

Used by the cutover protocol in
``docs/architecture/feature-flag-architecture.md`` §4.2. The operator runs
this script twice: once before flipping the flag, once after the soak
window. The output JSONs feed ``diff_baseline.py`` for hard-gate
verification.

This is operator-facing release-ops tooling — it lives under
``scripts/cutover/``, not in the ``kairix`` package (it is not shipped in
the published wheel).

Usage::

    python scripts/cutover/capture_baseline.py \\
        --flag obsidian_connector_primary \\
        --out /tmp/baseline-obsidian-pre.json

    # ... flip flag, soak ...

    python scripts/cutover/capture_baseline.py \\
        --flag obsidian_connector_primary \\
        --out /tmp/baseline-obsidian-post.json

    python scripts/cutover/diff_baseline.py \\
        --pre /tmp/baseline-obsidian-pre.json \\
        --post /tmp/baseline-obsidian-post.json --strict

Per-surface capture is resilient: if any individual surface fails
(missing CLI subcommand, error exit, unparseable JSON), the script
records a ``null`` for that surface and emits a warning. The diff tool
skips gates whose surface is missing.

JSON shape (frozen — diff_baseline depends on it):

    {
      "flag": "<flag-name>",
      "captured_at": "<ISO-8601 UTC>",
      "version": "<setuptools-scm version or 'unknown'>",
      "state": {
        "per_collection": [{"collection": str, "doc_count": int, "total_bytes": int}, ...],
        "content_hash_digest": "sha256:<hex>"
      } | null,
      "eval": {"reflib": {...}, "locomo": {...}} | null,
      "latency": {"p50_ms": float, "p95_ms": float, "p99_ms": float} | null,
      "sample_journey": [{"query": str, "top_paths": [str, ...]}, ...] | null
    }
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("cutover.capture_baseline")

ALL_SURFACES = ("state", "eval", "latency", "sample-journey")


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 with 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_version() -> str:
    """Return the kairix package version, or 'unknown' if it can't be read."""
    try:
        from importlib.metadata import version

        return version("kairix")
    except Exception:
        return "unknown"


def _load_config(config_path: Path) -> dict[str, Any]:
    """Read a YAML config file. Returns {} on any failure (caller logs).

    Falls back gracefully if the file is missing — the diff tool handles
    missing sample-journey by skipping the gate.
    """
    if not config_path.exists():
        logger.warning("config not found at %s; sample-journey will be skipped", config_path)
        return {}
    try:
        import yaml  # type: ignore[import-untyped]  # PyYAML is a project dep
    except ImportError:
        logger.warning("PyYAML not available; cannot read config %s", config_path)
        return {}
    try:
        with config_path.open() as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            logger.warning("config %s is not a mapping; ignoring", config_path)
            return {}
        return data
    except Exception as exc:
        logger.warning("failed to parse config %s: %s", config_path, exc)
        return {}


def _resolve_sqlite_path(config: dict[str, Any]) -> Path | None:
    """Resolve the SQLite documents-db path from config."""
    paths_cfg = config.get("paths", {}) if isinstance(config.get("paths"), dict) else {}
    candidate = paths_cfg.get("documents_db") or paths_cfg.get("sqlite_path")
    if candidate:
        return Path(candidate)
    return None


def _capture_state_from_sqlite(db_path: Path) -> dict[str, Any] | None:
    """Capture per-collection counts + content-hash digest from a SQLite DB.

    Returns ``None`` if the database cannot be opened or queried.
    """
    if not db_path.exists():
        logger.warning("state: sqlite db not found at %s", db_path)
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        logger.warning("state: cannot open %s: %s", db_path, exc)
        return None
    try:
        per_collection = _query_per_collection(conn)
        digest = _query_content_hash_digest(conn)
    finally:
        conn.close()
    if per_collection is None or digest is None:
        return None
    return {"per_collection": per_collection, "content_hash_digest": digest}


def _query_per_collection(conn: sqlite3.Connection) -> list[dict[str, Any]] | None:
    """Run the per-collection roll-up query. Returns None on schema mismatch."""
    sql = (
        "SELECT d.collection, COUNT(*) AS doc_count, "
        "COALESCE(SUM(LENGTH(c.doc)), 0) AS total_bytes "
        "FROM documents d JOIN content c ON c.hash = d.hash "
        "WHERE d.active = 1 GROUP BY d.collection ORDER BY d.collection"
    )
    try:
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        logger.warning("state: per-collection query failed: %s", exc)
        return None
    return [{"collection": row[0], "doc_count": int(row[1]), "total_bytes": int(row[2])} for row in rows]


def _query_content_hash_digest(conn: sqlite3.Connection) -> str | None:
    """SHA-256 of the sorted concatenation of content hashes."""
    try:
        rows = conn.execute("SELECT hash FROM documents WHERE active = 1 ORDER BY hash").fetchall()
    except sqlite3.Error as exc:
        logger.warning("state: content-hash query failed: %s", exc)
        return None
    hasher = hashlib.sha256()
    for (h,) in rows:
        if h is None:
            continue
        hasher.update(h.encode("utf-8") if isinstance(h, str) else bytes(h))
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def _run_cli_json(argv: list[str], timeout: int = 600) -> dict[str, Any] | None:
    """Invoke a CLI command, parse stdout as JSON. Returns None on any failure."""
    try:
        result = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("subprocess %s failed: %s", argv[:3], exc)
        return None
    if result.returncode != 0:
        logger.warning(
            "subprocess %s exited %d: stderr=%s",
            argv[:3],
            result.returncode,
            (result.stderr or "")[:400],
        )
        return None
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.warning("subprocess %s emitted non-JSON: %s", argv[:3], exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("subprocess %s emitted non-object JSON", argv[:3])
        return None
    return parsed


def _capture_benchmark_scores() -> dict[str, Any] | None:
    """Shell out to ``kairix benchmark`` for reflib + LoCoMo scores."""
    reflib = _run_cli_json(["kairix", "benchmark", "run", "--suite", "reflib", "--concurrency", "1", "--json"])
    locomo = _run_cli_json(["kairix", "benchmark", "run", "--suite", "locomo", "--concurrency", "1", "--json"])
    if reflib is None and locomo is None:
        return None
    out: dict[str, Any] = {}
    if reflib is not None:
        out["reflib"] = reflib
    if locomo is not None:
        out["locomo"] = locomo
    return out


def _capture_latency() -> dict[str, Any] | None:
    """Shell out to ``kairix probe`` and pull P50/P95/P99 latencies."""
    payload = _run_cli_json(["kairix", "probe", "--suite", "reflib", "--concurrency", "10", "--json"])
    if payload is None:
        return None
    return _extract_latency_percentiles(payload)


def _extract_latency_percentiles(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pick P50/P95/P99 fields out of a probe envelope (tolerant of nesting)."""
    candidates = [payload]
    for key in ("latency", "summary", "metrics", "stats"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for src in candidates:
        p50 = _coerce_float(src.get("p50_ms") or src.get("p50"))
        p95 = _coerce_float(src.get("p95_ms") or src.get("p95"))
        p99 = _coerce_float(src.get("p99_ms") or src.get("p99"))
        if p50 is not None and p95 is not None and p99 is not None:
            return {"p50_ms": p50, "p95_ms": p95, "p99_ms": p99}
    logger.warning("latency: could not find p50/p95/p99 in probe payload")
    return None


def _coerce_float(value: Any) -> float | None:
    """Coerce a possibly-numeric value to float, or None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _capture_sample_journey(config: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Run each operator-declared canonical query and capture top-5 doc paths."""
    cutover_cfg = config.get("cutover", {}) if isinstance(config.get("cutover"), dict) else {}
    queries = cutover_cfg.get("sample_queries")
    if not isinstance(queries, list) or not queries:
        logger.warning("sample-journey: no cutover.sample_queries in config; skipping surface")
        return None
    results: list[dict[str, Any]] = []
    for query in queries:
        if not isinstance(query, str) or not query.strip():
            continue
        results.append({"query": query, "top_paths": _run_sample_query(query)})
    return results if results else None


def _run_sample_query(
    query: str,
    runner: Callable[[list[str]], dict[str, Any] | None] | None = None,
) -> list[str]:
    """Invoke ``kairix search --json`` for a single query. Returns top-5 paths.

    ``runner`` is the dependency seam — tests pass an explicit callable
    so they never need to patch the module surface. The production path
    leaves ``runner=None``, which falls back to ``_run_cli_json``.
    """
    if runner is None:
        payload = _run_cli_json(["kairix", "search", "--json", query], timeout=120)
    else:
        payload = runner(["kairix", "search", "--json", query])
    if payload is None:
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    paths: list[str] = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or item.get("source") or item.get("doc_path")
        if isinstance(path, str):
            paths.append(path)
    return paths


def _parse_surfaces(raw: str) -> list[str]:
    """Parse the ``--surface`` CSV. ``all`` expands to every surface."""
    items = [s.strip() for s in raw.split(",") if s.strip()]
    if not items or "all" in items:
        return list(ALL_SURFACES)
    invalid = [s for s in items if s not in ALL_SURFACES]
    if invalid:
        raise ValueError(f"unknown surface(s): {invalid}. fix: pick from {list(ALL_SURFACES)} or 'all'.")
    return items


def _build_baseline(flag: str, config_path: Path, surfaces: list[str]) -> dict[str, Any]:
    """Build the full baseline JSON envelope for the requested surfaces."""
    config = _load_config(config_path)
    envelope: dict[str, Any] = {
        "flag": flag,
        "captured_at": _now_iso(),
        "version": _read_version(),
    }
    if "state" in surfaces:
        envelope["state"] = _maybe_capture_state(config)
    if "eval" in surfaces:
        envelope["eval"] = _capture_benchmark_scores()
    if "latency" in surfaces:
        envelope["latency"] = _capture_latency()
    if "sample-journey" in surfaces:
        envelope["sample_journey"] = _capture_sample_journey(config)
    return envelope


def _maybe_capture_state(config: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve sqlite path from config and capture state, or return None."""
    db_path = _resolve_sqlite_path(config)
    if db_path is None:
        logger.warning("state: no paths.documents_db in config; skipping surface")
        return None
    return _capture_state_from_sqlite(db_path)


def _write_envelope(envelope: dict[str, Any], out_path: Path) -> None:
    """Write the envelope to ``out_path`` (parents created)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fh:
        json.dump(envelope, fh, indent=2, sort_keys=True)
        fh.write("\n")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build + parse the CLI."""
    parser = argparse.ArgumentParser(
        prog="capture_baseline",
        description="Capture pre/post-flip baseline for a feature-flag cutover.",
    )
    parser.add_argument("--flag", required=True, help="Feature-flag name (snake_case).")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("kairix.config.yaml"),
        help="Config file to read (default: kairix.config.yaml).",
    )
    parser.add_argument(
        "--surface",
        default="all",
        help="Comma-separated surfaces (state,eval,latency,sample-journey,all).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns 0 on success (including partial captures)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    try:
        surfaces = _parse_surfaces(args.surface)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    envelope = _build_baseline(args.flag, args.config, surfaces)
    _write_envelope(envelope, args.out)
    captured = [s for s in ALL_SURFACES if envelope.get(s.replace("-", "_")) is not None]
    print(f"captured {len(captured)}/{len(ALL_SURFACES)} surfaces ({','.join(captured) or 'none'}) -> {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover — module-as-script entrypoint
    with contextlib.suppress(KeyboardInterrupt):
        sys.exit(main())
