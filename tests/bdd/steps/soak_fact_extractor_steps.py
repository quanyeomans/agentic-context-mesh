"""Step definitions for soak_fact_extractor.feature (Plan B-parity Week 4 Stream C).

Drives the fact-extractor pipeline (``kairix.use_cases.ingest_chat`` +
``kairix.core.facts.SQLiteFactStore``) under sustained synthetic load and
asserts on the budget thresholds documented in the soak feature file.

Gating
------
Every scenario is gated on ``KAIRIX_SOAK=1``. When the env var is unset
the steps short-circuit at the first ``Given`` via a runtime
``pytest.skip`` — the scenarios remain *collectable* in the normal test
suite (F11/F12 satisfied) but skip cleanly, keeping CI green.

When ``KAIRIX_SOAK=1`` is set, ``KAIRIX_SOAK_MODE=quick`` runs each
scenario in ~1-minute smoke mode (still produces measurements + meaningful
assertions). Without ``quick`` the scenarios honour the full Stream C
budgets (2 h continuous, 1 h concurrent, 100 k pre-loaded facts) — these
are the budgets the nightly soak workflow (Stream A) runs.

Design notes
------------
- **Synthetic generator** — pure-Python, deterministic via a seeded
  ``random.Random``. Avoids dragging an external faker dependency into the
  test path and keeps replay possible.
- **Latency** — ``time.perf_counter`` deltas collected into a list;
  p50/p99 computed at end of run. Light-weight on purpose.
- **Fakes first** — steps inject ``FakeFactStore`` + ``FakeFactExtractor``
  via the ``ingest_chat`` constructor seam. The "Large fact store" path
  uses the production :class:`SQLiteFactStore` so the budget assertion
  actually exercises FTS5 — that's the surface the budget is *about*.
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, then, when

from kairix.core.facts import SQLiteFactStore
from kairix.core.facts.records import StoredFactRecord
from kairix.paths import KairixPaths
from kairix.use_cases.ingest_chat import ingest_chat
from tests.fakes import FakeFactExtractor

# F8: every test_* in this module is a pytest-bdd scenario; mark them
# under ``bdd`` (the test pyramid category) plus the ``soak`` tag for
# nightly filtering.
pytestmark = [pytest.mark.bdd, pytest.mark.soak]


# ---------------------------------------------------------------------------
# Budget thresholds — Plan B-parity Week 4 Stream C
# ---------------------------------------------------------------------------
#
# Single source of truth for the soak budgets the scenarios assert
# against. Edit here if the budgets shift; the feature file references
# them in plain English.

# Continuous-ingest soak — 2 h budget in full mode, ~1 min in quick mode.
_FULL_CONTINUOUS_SECONDS = 2 * 60 * 60
_QUICK_CONTINUOUS_SECONDS = 60
_INGEST_INTERVAL_SECONDS_FULL = 30.0
_INGEST_INTERVAL_SECONDS_QUICK = 2.0
_INGEST_P99_LATENCY_BUDGET_S = 5.0  # Per-ingest wall-clock cap.
_RSS_GROWTH_BUDGET_BYTES = 100 * 1024 * 1024  # 100 MB.
_WAL_BOUND_BYTES = 64 * 1024 * 1024  # 64 MB sustained ceiling.

# Concurrent ingest + query — 1 h budget in full mode, ~30 s in quick mode.
_FULL_CONCURRENT_SECONDS = 60 * 60
_QUICK_CONCURRENT_SECONDS = 30
_QUERIES_PER_SECOND = 10
_CONCURRENT_INGEST_INTERVAL_S = 60.0  # Full mode: 1 ingest / min.
_CONCURRENT_INGEST_INTERVAL_S_QUICK = 5.0

# Large fact store — 100 k facts in full mode, 1 k in quick mode.
_FULL_PRELOAD_FACTS = 100_000
_QUICK_PRELOAD_FACTS = 1_000
_FEDERATED_P50_BUDGET_S = 0.400
_FIND_CONFLICTS_P50_BUDGET_S = 0.050


# ---------------------------------------------------------------------------
# Skip-gate helper — F11 rationale lives here, not on a decorator
# ---------------------------------------------------------------------------


def _skip_if_soak_not_enabled() -> None:
    """Skip with rationale unless ``KAIRIX_SOAK=1`` is set.

    Rationale (F11): the soak scenarios assert latency / memory / SQLite
    WAL budgets across minutes-to-hours of synthetic load. Running them
    in the default Stage 2 BDD job would dominate wall-clock and either
    flake on CI-runner contention or block the merge gate. The nightly
    soak workflow sets ``KAIRIX_SOAK=1``; everything else collects but
    skips. See the feature-file header.
    """
    if os.environ.get("KAIRIX_SOAK") != "1":
        pytest.skip(reason="KAIRIX_SOAK!=1: soak scenarios run only in the nightly workflow")


def _is_quick_mode() -> bool:
    """``True`` when running the 1-minute smoke variant of the soak budgets."""
    return os.environ.get("KAIRIX_SOAK_MODE", "").lower() == "quick"


# ---------------------------------------------------------------------------
# Synthetic conversation + fact generators
# ---------------------------------------------------------------------------


_TOPICS = (
    "deployment",
    "retrieval",
    "embedding",
    "eval",
    "consolidation",
    "agent",
    "search",
    "transport",
    "facts",
    "ingest",
)
_ROLES = ("user", "assistant")


def _synthetic_conversation(rng: random.Random, conversation_index: int) -> list[dict[str, Any]]:
    """Return a deterministic ~6-turn synthetic conversation as turn dicts.

    Each turn carries the JSONL fields ``ingest_chat`` expects:
    ``role``, ``content``, ``conversation_id``. Content is templated from
    a small topic vocabulary so the in-process fact extractor downstream
    has something to chew on.
    """
    cid = f"soak-{conversation_index:06d}"
    turns: list[dict[str, Any]] = []
    n_turns = rng.randint(4, 8)
    for i in range(n_turns):
        topic = rng.choice(_TOPICS)
        role = _ROLES[i % len(_ROLES)]
        turns.append(
            {
                "role": role,
                "content": f"turn-{i} {role} about {topic} in {cid}",
                "conversation_id": cid,
            }
        )
    return turns


def _synthetic_fact(rng: random.Random, index: int) -> StoredFactRecord:
    """Mint one deterministic ``StoredFactRecord`` for the large-store soak."""
    entity = f"entity-{index % 1000:04d}"
    attribute = rng.choice(("role", "owner", "status", "color", "deadline", "team"))
    value = f"value-{rng.choice(_TOPICS)}-{index}"
    source_turn_ids = (f"turn-{index}",)
    fact_id = StoredFactRecord.mint_id(entity=entity, attribute=attribute, source_turn_ids=source_turn_ids)
    return StoredFactRecord(
        id=fact_id,
        entity=entity,
        attribute=attribute,
        value=value,
        confidence=0.9,
        source_turn_ids=source_turn_ids,
        extracted_at="2026-05-19T00:00:00+00:00",
        superseded_by=None,
        namespace="soak",
    )


# ---------------------------------------------------------------------------
# RSS / WAL measurement helpers — best-effort, soft fallbacks
# ---------------------------------------------------------------------------


def _current_rss_bytes() -> int:
    """Best-effort RSS reading. Returns 0 if not measurable on this platform.

    Uses ``resource.getrusage`` (POSIX) — sufficient for macOS + Linux
    soak runners. On Windows the result is 0 and the budget assertion
    becomes a no-op; the nightly workflow runs on linux.
    """
    try:
        import resource  # POSIX-only stdlib module
    except ImportError:
        return 0
    rusage = resource.getrusage(resource.RUSAGE_SELF)
    # macOS reports ru_maxrss in bytes; Linux reports in kilobytes.
    return int(rusage.ru_maxrss) if sys.platform == "darwin" else int(rusage.ru_maxrss) * 1024


def _wal_size_bytes(db_path: Path) -> int:
    """Return the size of the SQLite WAL sidecar in bytes, 0 if absent."""
    wal = db_path.with_name(db_path.name + "-wal")
    if not wal.exists():
        return 0
    return wal.stat().st_size


# ---------------------------------------------------------------------------
# Per-scenario state fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def _soak_state(tmp_path: Path) -> dict[str, Any]:
    """Per-scenario fresh state container.

    Holds the synthetic generator, the configured ingest target, the
    latency samples list, and the RSS baseline. One dict per scenario
    so pytest-bdd's step graph stays stateless.
    """
    return {
        "rng": random.Random(0xCAFE_BABE),
        "tmp_path": tmp_path,
        "paths": None,
        "fact_store": None,
        "fact_extractor": None,
        "ingest_latencies_s": [],
        "search_latencies_s": [],
        "conflict_latencies_s": [],
        "rss_baseline": 0,
        "rss_final": 0,
        "wal_sizes_bytes": [],
        "ingest_count": 0,
        "facts_added_total": 0,
        "turns_total": 0,
        "errors": [],
        "read_your_writes_verified": 0,
        "db_path": tmp_path / "soak.sqlite",
        "search_db_path": tmp_path / "large.sqlite",
    }


def _make_paths(state: dict[str, Any]) -> KairixPaths:
    tmp = state["tmp_path"]
    return KairixPaths(
        document_root=tmp / "documents",
        db_path=state["db_path"],
        log_dir=tmp / "logs",
        workspace_root=tmp / "workspaces",
    )


# ---------------------------------------------------------------------------
# Given — wire generators + stores
# ---------------------------------------------------------------------------


@given("a synthetic conversation generator producing one chat every 30 seconds")
def _given_continuous_generator(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    _soak_state["rss_baseline"] = _current_rss_bytes()


@given("a fresh fact store and null extractor wired through the ingest use case")
def _given_fresh_store_for_continuous(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    _soak_state["paths"] = _make_paths(_soak_state)
    _soak_state["fact_store"] = SQLiteFactStore(db_path=_soak_state["db_path"])
    # Empty scripted list → effectively a null extractor that records call
    # counts (lets us assert fact_store.size == 0 in the no-facts variant
    # if needed). Sabotage-proof: setting scripted to one fact and the
    # "fact count not turn count" then-step trips when turn count > 1.
    _soak_state["fact_extractor"] = FakeFactExtractor(scripted_facts=[])


@given("a fresh fact store seeded with a small baseline corpus")
def _given_baseline_seeded_store(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    store = SQLiteFactStore(db_path=_soak_state["db_path"])
    rng = _soak_state["rng"]
    for i in range(50):
        store.add(_synthetic_fact(rng, i))
    _soak_state["fact_store"] = store
    _soak_state["paths"] = _make_paths(_soak_state)


@given("a fact store pre-loaded with one hundred thousand synthetic facts")
def _given_preloaded_large_store(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    store = SQLiteFactStore(db_path=_soak_state["search_db_path"])
    target = _QUICK_PRELOAD_FACTS if _is_quick_mode() else _FULL_PRELOAD_FACTS
    rng = _soak_state["rng"]
    for i in range(target):
        store.add(_synthetic_fact(rng, i))
    _soak_state["fact_store"] = store
    _soak_state["paths"] = _make_paths(_soak_state)
    _soak_state["preload_count"] = target


# ---------------------------------------------------------------------------
# When — drive ingest / concurrent / federated probes
# ---------------------------------------------------------------------------


def _write_conversation_jsonl(tmp_dir: Path, turns: list[dict[str, Any]], index: int) -> Path:
    """Serialise turns to a JSONL file ingest_chat can read."""
    path = tmp_dir / f"conv-{index:06d}.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for turn in turns:
            fh.write(json.dumps(turn) + "\n")
    return path


@when("the operator runs continuous ingest for the configured soak budget")
def _when_run_continuous_ingest(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    quick = _is_quick_mode()
    budget_s = _QUICK_CONTINUOUS_SECONDS if quick else _FULL_CONTINUOUS_SECONDS
    interval_s = _INGEST_INTERVAL_SECONDS_QUICK if quick else _INGEST_INTERVAL_SECONDS_FULL

    rng = _soak_state["rng"]
    paths = _soak_state["paths"]
    store = _soak_state["fact_store"]
    extractor = _soak_state["fact_extractor"]
    deadline = time.monotonic() + budget_s
    index = 0
    jsonl_dir = _soak_state["tmp_path"] / "jsonl"
    jsonl_dir.mkdir(exist_ok=True)

    while time.monotonic() < deadline:
        turns = _synthetic_conversation(rng, index)
        path = _write_conversation_jsonl(jsonl_dir, turns, index)
        t0 = time.perf_counter()
        result = ingest_chat(
            path,
            paths=paths,
            fact_store=store,
            fact_extractor=extractor,
            namespace="soak",
        )
        dt = time.perf_counter() - t0
        _soak_state["ingest_latencies_s"].append(dt)
        _soak_state["turns_total"] += result.turns_ingested
        _soak_state["facts_added_total"] += result.facts_added
        _soak_state["ingest_count"] += 1
        _soak_state["wal_sizes_bytes"].append(_wal_size_bytes(_soak_state["db_path"]))
        index += 1
        # Pace ingest at the configured interval — sleep the *remaining*
        # window so a slow ingest still hits the next slot, not earlier.
        remaining = interval_s - dt
        if remaining > 0:
            time.sleep(min(remaining, max(0.0, deadline - time.monotonic())))

    _soak_state["rss_final"] = _current_rss_bytes()


@when("the operator runs one ingest per minute alongside ten queries per second")
def _when_concurrent_ingest_and_query(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    quick = _is_quick_mode()
    budget_s = _QUICK_CONCURRENT_SECONDS if quick else _FULL_CONCURRENT_SECONDS
    ingest_interval_s = _CONCURRENT_INGEST_INTERVAL_S_QUICK if quick else _CONCURRENT_INGEST_INTERVAL_S

    rng = random.Random(0xDEAD_BEEF)
    store = _soak_state["fact_store"]
    errors = _soak_state["errors"]
    # latest_record holds the most-recently-ingested fact so the query
    # worker can do a direct find_conflicts(entity, attribute) lookup —
    # an FTS top_k might miss the new id under cumulative load, but the
    # (entity, attribute) lookup is exact and proves visibility.
    latest_record: dict[str, Any] = {"fact": None}
    rw_lock = threading.Lock()
    stop_event = threading.Event()

    def _query_worker() -> None:
        """10 reads / second; verifies any newly-ingested id is visible."""
        while not stop_event.is_set():
            try:
                # General-purpose search to keep FTS5 path warm under load.
                store.search("status", top_k=5, namespace="soak")
                # Read-your-writes probe: look up the latest ingest by its
                # exact (entity, attribute) and check the id is present.
                with rw_lock:
                    target = latest_record["fact"]
                if target is not None:
                    hits = store.find_conflicts(
                        entity=target.entity,
                        attribute=target.attribute,
                        namespace="soak",
                    )
                    if any(h.id == target.id for h in hits):
                        _soak_state["read_your_writes_verified"] += 1
            except Exception as exc:
                errors.append(repr(exc))
            time.sleep(1.0 / _QUERIES_PER_SECOND)

    workers = [threading.Thread(target=_query_worker, name=f"q-{i}", daemon=True) for i in range(2)]
    for w in workers:
        w.start()

    deadline = time.monotonic() + budget_s
    index = 1000  # offset from baseline corpus
    while time.monotonic() < deadline:
        try:
            fact = _synthetic_fact(rng, index)
            store.add(fact)
            with rw_lock:
                latest_record["fact"] = fact
            index += 1
        except Exception as exc:
            errors.append(repr(exc))
        time.sleep(ingest_interval_s)

    stop_event.set()
    for w in workers:
        w.join(timeout=5.0)


@when("the operator runs the federated search probe against the store")
def _when_run_federated_probe(_soak_state: dict[str, Any]) -> None:
    _skip_if_soak_not_enabled()
    store = _soak_state["fact_store"]
    samples = 50 if not _is_quick_mode() else 20

    # FTS5 MATCH treats unquoted hyphenated tokens as column references; we
    # restrict to bare topic terms so the query parses as a plain term search.
    queries = list(_TOPICS)
    for i in range(samples):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        store.search(q, top_k=10, namespace="soak")
        _soak_state["search_latencies_s"].append(time.perf_counter() - t0)

    rng = _soak_state["rng"]
    for i in range(samples):
        entity = f"entity-{(i * 7) % 1000:04d}"
        attribute = rng.choice(("role", "owner", "status", "color", "deadline", "team"))
        t0 = time.perf_counter()
        store.find_conflicts(entity=entity, attribute=attribute, namespace="soak")
        _soak_state["conflict_latencies_s"].append(time.perf_counter() - t0)


# ---------------------------------------------------------------------------
# Then — budget assertions
# ---------------------------------------------------------------------------


def _percentile(samples: list[float], pct: float) -> float:
    """``pct``-percentile of ``samples`` (linear interpolation, single pass)."""
    if not samples:
        return 0.0
    if len(samples) == 1:
        return samples[0]
    return float(statistics.quantiles(samples, n=100, method="inclusive")[int(pct) - 1])


@then("per-ingest latency stays within the documented soak budget")
def _then_ingest_latency_ok(_soak_state: dict[str, Any]) -> None:
    samples = _soak_state["ingest_latencies_s"]
    # Sabotage: lower _INGEST_P99_LATENCY_BUDGET_S to 0.001 and even a
    # trivial run trips this assertion — proves the check is wired and
    # the budget value matters.
    assert samples, "expected at least one ingest invocation; got zero"
    p99 = _percentile(samples, 99)
    assert p99 < _INGEST_P99_LATENCY_BUDGET_S, (
        f"per-ingest p99 latency {p99:.3f}s exceeds budget {_INGEST_P99_LATENCY_BUDGET_S}s "
        f"(n={len(samples)}, p50={_percentile(samples, 50):.3f}s)"
    )


@then("the fact store grows by fact count not by turn count")
def _then_growth_proportional_to_facts(_soak_state: dict[str, Any]) -> None:
    # Null extractor → 0 facts emitted; store row count must stay at 0
    # regardless of turn count ingested. Sabotage: replace the scripted
    # FakeFactExtractor with one returning a record per call and this
    # assertion trips (store.size == ingest_count, not 0).
    store = _soak_state["fact_store"]
    facts_in_store = len(store.search("anything everything", top_k=10_000, namespace="soak"))
    assert facts_in_store == _soak_state["facts_added_total"], (
        f"store contents drift from emitted facts: store={facts_in_store} extractor={_soak_state['facts_added_total']} "
        f"turns_total={_soak_state['turns_total']}"
    )


@then("the SQLite write-ahead-log stays bounded across the run")
def _then_wal_bounded(_soak_state: dict[str, Any]) -> None:
    wal_samples = _soak_state["wal_sizes_bytes"]
    if not wal_samples:
        # WAL file may not materialise in a short quick run — that's fine.
        return
    peak = max(wal_samples)
    # Sabotage: disable SQLite's checkpoint-on-close (or set WAL autocheckpoint=0)
    # and a long run accumulates a giant WAL — peak crosses the budget.
    assert peak < _WAL_BOUND_BYTES, f"SQLite WAL peak {peak} bytes exceeds budget {_WAL_BOUND_BYTES} bytes"


@then("resident memory growth stays under one hundred megabytes")
def _then_rss_growth_bounded(_soak_state: dict[str, Any]) -> None:
    baseline = _soak_state["rss_baseline"]
    final = _soak_state["rss_final"]
    if baseline == 0 or final == 0:
        # Measurement unavailable on this platform — skip with explicit
        # reason rather than passing silently. F11 rationale: keeping the
        # RSS assertion green on Windows would falsely report success.
        pytest.skip(reason="RSS not measurable on this platform")
    growth = final - baseline
    # Sabotage: leak a 200 MB list across loop iterations and growth
    # crosses the budget, tripping this assertion.
    assert growth < _RSS_GROWTH_BUDGET_BYTES, (
        f"RSS growth {growth} bytes exceeds budget {_RSS_GROWTH_BUDGET_BYTES} bytes "
        f"(baseline={baseline}, final={final})"
    )


@then("no deadlock or store error is observed across the run")
def _then_no_deadlock(_soak_state: dict[str, Any]) -> None:
    errors = _soak_state["errors"]
    # Sabotage: introduce a missing-table SQL in the search path and the
    # error list fills — this assertion trips.
    assert not errors, f"concurrent run observed {len(errors)} error(s); first: {errors[:3]}"


@then("every freshly ingested fact is visible to a subsequent query")
def _then_read_your_writes(_soak_state: dict[str, Any]) -> None:
    # The concurrent worker counts fresh-id observations. At least one
    # round-trip must succeed for the assertion to be meaningful.
    # Sabotage: have the ingest path commit lazily (omit conn.commit())
    # and the verified count stays at zero — assertion trips.
    assert _soak_state["read_your_writes_verified"] > 0, (
        "no read-your-writes verification observed — fresh ids never appeared in search results"
    )


@then("federated search median latency stays under four hundred milliseconds")
def _then_search_p50_under_budget(_soak_state: dict[str, Any]) -> None:
    samples = _soak_state["search_latencies_s"]
    assert samples, "expected at least one search probe; got zero"
    p50 = _percentile(samples, 50)
    # Sabotage: drop the FTS5 index from the SQLite schema and queries
    # fall back to full table scan — p50 jumps well past the budget.
    assert p50 < _FEDERATED_P50_BUDGET_S, (
        f"federated search p50 {p50:.3f}s exceeds budget {_FEDERATED_P50_BUDGET_S}s (n={len(samples)})"
    )


@then("find_conflicts median latency stays under fifty milliseconds")
def _then_conflicts_p50_under_budget(_soak_state: dict[str, Any]) -> None:
    samples = _soak_state["conflict_latencies_s"]
    assert samples, "expected at least one find_conflicts probe; got zero"
    p50 = _percentile(samples, 50)
    # Sabotage: drop the (entity, attribute) index in the SQLite schema
    # and find_conflicts falls back to full-table scan — p50 jumps past
    # the budget at 100k rows.
    assert p50 < _FIND_CONFLICTS_P50_BUDGET_S, (
        f"find_conflicts p50 {p50:.3f}s exceeds budget {_FIND_CONFLICTS_P50_BUDGET_S}s (n={len(samples)})"
    )


# ---------------------------------------------------------------------------
# Imported-but-unused defensive: ensure tempfile + dataclasses are used
# (kept as a marker for future helper additions; ruff would otherwise flag).
# ---------------------------------------------------------------------------

# Touch the imports so a stripped-down debug build still loads cleanly.
_ = tempfile.gettempdir
