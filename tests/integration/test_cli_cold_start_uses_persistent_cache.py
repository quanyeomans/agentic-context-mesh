"""Cold-start cache rehydrate test for #411 Phase 2.

When no MCP server is reachable (the case #411 Phase 1's
``kairix-via-mcp`` routing falls back from), every fresh ``kairix
<subcommand>`` is a cold process. Phase 2 persists the three pipeline
caches to ``~/.cache/kairix/*.sqlite`` so the second cold start serves
from the SQLite layer instead of paying the full search / synthesis
cost again.

This integration test exercises the round-trip end-to-end through a
subprocess (no in-process cache state shared), measuring the latency
difference between an empty-cache cold start and a populated-cache
cold start.

Approach: invoke a tiny Python subprocess that drives the production
:class:`QueryResultCache` against a deterministic tmp-path cache file
in two phases. Phase 1 writes a SearchResult; phase 2 reads it from a
fresh process. Phase 2's wall-clock is dominated by the cache rehydrate
+ get, not by any pipeline build.

The wall-clock improvement is informational (CI-flaky); the
load-bearing assertion is the second subprocess returning the right
payload from disk, proving the rehydrate worked across processes.

F-rule discipline:
  - F1/F2: subprocess pattern — no @patch, no monkeypatch.
  - F8: ``pytestmark = pytest.mark.integration``.
  - F30: outcome assertions on stdout content + exit code.
  - F47: integration test composes via the production
    :class:`QueryResultCache` API (the factory's persistent-cache seam).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


# The subprocess script is a self-contained driver that exercises the
# real :class:`QueryResultCache` against an injected cache path. Two
# subprocess invocations share the on-disk file but not in-process
# state — exactly the cold-CLI-start scenario.
_SUBPROCESS_DRIVER = """
import json
import sys
import time
from pathlib import Path

from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchResult
from kairix.core.search.query_cache import QueryResultCache, make_cache_key
from kairix.core.search.scope import Scope

mode = sys.argv[1]
path = Path(sys.argv[2])
cfg_hash = sys.argv[3]

t0 = time.perf_counter()
cache = QueryResultCache(path=path, cfg_hash=cfg_hash)
build_elapsed_ms = (time.perf_counter() - t0) * 1000.0

key = make_cache_key("agent-alpha briefing", Scope.SHARED_AGENT, "agent-alpha", None)

if mode == "write":
    sr = SearchResult(
        query="agent-alpha briefing",
        intent=QueryIntent.SEMANTIC,
        latency_ms=42.0,
        bm25_count=5,
    )
    cache.put(key, sr)
    cache.close()
    print(json.dumps({"mode": "write", "build_ms": build_elapsed_ms, "size": 1}))
elif mode == "read":
    t1 = time.perf_counter()
    sr = cache.get(key)
    read_elapsed_ms = (time.perf_counter() - t1) * 1000.0
    cache.close()
    print(json.dumps({
        "mode": "read",
        "build_ms": build_elapsed_ms,
        "read_ms": read_elapsed_ms,
        "hit": sr is not None,
        "query": sr.query if sr else None,
        "intent": sr.intent.value if sr else None,
        "latency_ms": sr.latency_ms if sr else None,
        "bm25_count": sr.bm25_count if sr else None,
    }))
else:
    raise ValueError(f"unknown mode {mode!r}")
"""


def _run_driver(mode: str, path: Path, cfg_hash: str) -> tuple[dict[str, object], float]:
    """Run the subprocess driver and return (parsed_envelope, wall_clock_ms)."""
    t0 = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_DRIVER, mode, str(path), cfg_hash],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert proc.returncode == 0, (
        f"subprocess driver mode={mode!r} exited {proc.returncode}\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}"
    )
    envelope = json.loads(proc.stdout.strip())
    return envelope, elapsed_ms


def test_cold_start_rehydrates_from_persistent_cache(tmp_path: Path) -> None:
    """A fresh process reads a SearchResult the previous process wrote.

    Phase 1 (write): cold subprocess constructs a cache against the
    tmp file, writes one entry, exits.
    Phase 2 (read): completely separate cold subprocess reconstructs
    the cache against the SAME tmp file, calls ``get`` for the same
    key, observes the rehydrated SearchResult.

    The wall-clock check is informational — we want to see Phase 2 hit
    the disk cache (so its read_ms is small), but flaky CI shouldn't
    block on a hard latency threshold. The load-bearing assertion is
    the cross-process rehydrate envelope shape.

    Sabotage-proof (executed locally):
      Removed the ``self._upsert_persisted(key, now, value)`` line
      from ``QueryResultCache.put``. Re-ran the test; Phase 2 envelope
      reported ``hit=False`` (cache file existed but had no rows in
      it). Restored.
    """
    cache_file = tmp_path / "query_cache.sqlite"
    cfg_hash = "cold-start-cfg"

    # Phase 1 — write.
    write_envelope, _ = _run_driver("write", cache_file, cfg_hash)
    assert write_envelope == {"mode": "write", "build_ms": pytest.approx(write_envelope["build_ms"]), "size": 1}, (
        f"phase 1 (write) envelope shape unexpected: {write_envelope}"
    )
    assert cache_file.exists(), f"phase 1 did not create cache file at {cache_file}"
    assert cache_file.stat().st_size > 0, "phase 1 created an empty cache file"

    # Phase 2 — read from a completely fresh process.
    read_envelope, _ = _run_driver("read", cache_file, cfg_hash)
    assert read_envelope["mode"] == "read"
    assert read_envelope["hit"] is True, (
        f"phase 2 (read) did NOT rehydrate from disk — envelope: {read_envelope}. "
        "fix: keep self._upsert_persisted in QueryResultCache.put + the "
        "_SELECT_BY_CFG_SQL replay branch in _open_and_replay. "
        "run: pytest tests/integration/test_cli_cold_start_uses_persistent_cache.py"
    )
    assert read_envelope["query"] == "agent-alpha briefing"
    assert read_envelope["intent"] == "semantic"
    assert read_envelope["latency_ms"] == pytest.approx(42.0)
    assert read_envelope["bm25_count"] == 5


def test_cold_start_with_different_cfg_hash_does_not_rehydrate(tmp_path: Path) -> None:
    """A fresh process with a NEW cfg_hash sees the cache as empty.

    Models the operator-action of swapping the provider or fusion
    strategy between two CLI invocations: the persisted rows are no
    longer valid for the new pipeline shape; the rehydrate path
    silently drops them.

    Sabotage-proof (executed locally):
      Replaced ``_SELECT_BY_CFG_SQL`` with a query that drops the
      ``WHERE cfg_hash = ?`` clause. Re-ran; Phase 2 (cfg-b) reported
      ``hit=True`` even though it should have been blind to cfg-a's
      rows. Restored.
    """
    cache_file = tmp_path / "query_cache.sqlite"

    # Phase 1 — write under cfg-a.
    _run_driver("write", cache_file, "cfg-a")
    assert cache_file.exists()

    # Phase 2 — read under cfg-b. Different cfg_hash = no rehydrate.
    read_envelope, _ = _run_driver("read", cache_file, "cfg-b")
    assert read_envelope["hit"] is False, (
        f"phase 2 (cfg-b) saw cfg-a's rows — config-change invalidation "
        f"broken. envelope: {read_envelope}. "
        "fix: keep the WHERE cfg_hash = ? clause in _SELECT_BY_CFG_SQL."
    )
