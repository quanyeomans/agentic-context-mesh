"""Unit tests for ``kairix.quality.eval.chunk_stats`` (public surface only).

Drives every behaviour through the public functions ``collect_chunk_sizes``
/ ``compute_stats`` / ``render_human`` / ``emit_chunk_stats`` rather than
reaching into the underscore-prefixed helpers — F5 / engineering-standards
discipline (no internal-name imports in tests).

Coverage stays high because each public call exercises the helper chain:

* The DB-driven tests seed paths + collections that the source-type
  derivation must classify (every documented extension is covered).
* The percentile semantics surface through the rendered stats — a
  100-element fixture set checks p50 / p95 / p99 in one assertion.

Sabotage proofs noted per test.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.quality.eval.chunk_stats import (
    ChunkStats,
    collect_chunk_sizes,
    compute_stats,
    emit_chunk_stats,
    render_human,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_db_via_paths(db_path: Path, rows: list[tuple[str, str, int, int]]) -> None:
    """Insert one document per ``rows`` entry.

    Each row is ``(path, collection, doc_size_chars, chunk_count)`` and
    seeds matching ``content`` / ``documents`` / ``content_vectors``.
    """
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    cur = db.cursor()
    for i, (path, collection, doc_size, chunk_count) in enumerate(rows):
        digest = f"hash-{i}"
        cur.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (digest, "x" * doc_size))
        cur.execute(
            "INSERT INTO documents (path, title, collection, hash, created_at, modified_at, active) "
            "VALUES (?, ?, ?, ?, ?, ?, 1)",
            (path, f"doc-{i}", collection, digest, "2026-05-01", "2026-05-01"),
        )
        for seq in range(chunk_count):
            cur.execute(
                "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
                (digest, seq, seq * 100, "test-model", "2026-05-01T00:00:00Z"),
            )
    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# Source-type derivation via the public collect_chunk_sizes path
# ---------------------------------------------------------------------------


def test_collect_resolves_documented_extensions(tmp_path: Path) -> None:
    """Every documented file extension routes to its canonical type slug.

    Sabotage: drop an entry from ``_EXTENSION_TO_SOURCE_TYPE`` — the
    type set assert fires because the dropped slug is missing.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(
        db_path,
        [
            ("notes/agent.md", "notes", 100, 1),
            ("decks/q3.pptx", "decks", 100, 1),
            ("policies/handbook.docx", "policies", 100, 1),
            ("books/ledger.xlsx", "books", 100, 1),
            ("books/legacy.xls", "books", 100, 1),
            ("mail/m1.eml", "mail", 100, 1),
            ("events/standup.ics", "events", 100, 1),
            ("events/sync.ical", "events", 100, 1),
        ],
    )
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    assert set(sizes.keys()) == {"markdown", "pptx", "docx", "xlsx", "email", "calendar"}


def test_collect_routes_unknown_extension_to_unknown(tmp_path: Path) -> None:
    """An unknown extension lands in the ``unknown`` bucket.

    Sabotage: change the fallback to return "markdown" — the assert
    that ``unknown`` is in the keys fires.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(db_path, [("photo.jpg", "media", 100, 1)])
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    assert "unknown" in sizes


def test_collect_routes_no_extension_path_to_unknown(tmp_path: Path) -> None:
    """A path with no extension and no fixture-collection routes to ``unknown``.

    Sabotage: remove the ``if len(suffix) != 2`` guard — the rsplit
    fallback returns the path itself and the type set changes.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(db_path, [("README", "misc", 100, 1)])
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    assert "unknown" in sizes


def test_collect_per_type_fixtures_unknown_subtype_falls_to_extension(tmp_path: Path) -> None:
    """A per-type-fixtures path whose subtype isn't in the slug set falls back to the extension.

    Sabotage: remove the ``if type_slug in _EXTENSION_TO_SOURCE_TYPE.values()``
    guard — the unknown subtype short-circuits and the assert that
    ``markdown`` is in the keys fires.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(
        db_path,
        [("per-type-fixtures/bogus-type/note.md", "per-type-fixtures/bogus-type", 100, 1)],
    )
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    # collection check rejects "bogus-type"; extension check picks .md → markdown
    assert "markdown" in sizes


def test_collect_per_type_fixtures_collection_wins(tmp_path: Path) -> None:
    """A per-type-fixtures collection drives the source-type tag.

    Even when the path has no extension, the collection's
    ``per-type-fixtures/<type>`` prefix locks the slug.

    Sabotage: drop the ``_collection_to_source_type`` branch — the
    no-extension path falls back to ``unknown`` and the assert fires.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(
        db_path,
        [
            ("per-type-fixtures/pptx/canary", "per-type-fixtures/pptx", 100, 2),
            ("per-type-fixtures/email/m01", "per-type-fixtures/email", 100, 1),
        ],
    )
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    assert set(sizes.keys()) == {"pptx", "email"}


def test_collect_skips_zero_chunk_documents(tmp_path: Path) -> None:
    """A document with no chunks does not contribute a zero-size row.

    Sabotage: remove the ``if chunk_count == 0: continue`` — the
    sizes mapping contains a "markdown" key it shouldn't.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(db_path, [("per-type-fixtures/markdown/note.md", "per-type-fixtures/markdown", 100, 0)])
    db = sqlite3.connect(str(db_path))
    try:
        sizes = collect_chunk_sizes(db)
    finally:
        db.close()
    assert sizes == {}


def test_collect_excludes_inactive_documents(tmp_path: Path) -> None:
    """Inactive documents (``active=0``) do not contribute chunks.

    Sabotage: drop ``WHERE d.active = 1`` from the SQL — the chunks
    from the inactive row land in the count.
    """
    db = sqlite3.connect(str(tmp_path / "k.sqlite"))
    create_schema(db)
    cur = db.cursor()
    cur.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", ("h-inactive", "x" * 500))
    cur.execute(
        "INSERT INTO documents (path, title, collection, hash, created_at, modified_at, active) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        (
            "per-type-fixtures/markdown/old.md",
            "old",
            "per-type-fixtures/markdown",
            "h-inactive",
            "2025-01-01",
            "2025-01-01",
        ),
    )
    cur.execute(
        "INSERT INTO content_vectors (hash, seq, pos, model, embedded_at) VALUES (?, ?, ?, ?, ?)",
        ("h-inactive", 0, 0, "m", "2025-01-01"),
    )
    db.commit()
    sizes = collect_chunk_sizes(db)
    db.close()
    assert sizes == {}


# ---------------------------------------------------------------------------
# Percentile semantics through compute_stats
# ---------------------------------------------------------------------------


def test_compute_stats_percentiles_match_nearest_rank() -> None:
    """1..100 chunks → p50=50, p95=95, p99=99 (nearest-rank).

    Sabotage: switch the rank formula to ``floor(pct * n)`` — the
    p95 assert fires.
    """
    by_type = {"markdown": list(range(1, 101))}
    stats = compute_stats(by_type)
    assert len(stats) == 1
    assert stats[0].n == 100
    assert stats[0].p50 == 50
    assert stats[0].p95 == 95
    assert stats[0].p99 == 99
    assert stats[0].mean == pytest.approx(50.5, abs=1e-3)


def test_compute_stats_returns_sorted_list() -> None:
    """``compute_stats`` projects the dict into a sorted list.

    Sabotage: drop the ``sorted(by_type.items())`` — the per-type
    ordering becomes nondeterministic and the assert fires.
    """
    by_type = {"pptx": [100, 200], "markdown": [500, 600, 700]}
    stats = compute_stats(by_type)
    assert [s.source_type for s in stats] == ["markdown", "pptx"]
    assert stats[0].n == 3
    assert stats[1].n == 2


def test_compute_stats_handles_empty_input() -> None:
    """Zero types → zero rows.

    Sabotage: remove the iteration and replace with a hard-coded list
    — the empty assert fires.
    """
    assert compute_stats({}) == []


def test_compute_stats_zero_chunks_returns_zero_summary() -> None:
    """A type with zero chunks emits an all-zero ChunkStats row.

    Sabotage: drop the ``if n == 0`` guard in ``_summarise`` — the
    division by zero raises and the assert never runs.
    """
    by_type = {"markdown": []}
    stats = compute_stats(by_type)
    assert len(stats) == 1
    assert stats[0].n == 0
    assert stats[0].mean == 0.0
    assert stats[0].p50 == 0
    assert stats[0].p95 == 0
    assert stats[0].p99 == 0


def test_compute_stats_single_element_handles_extreme_percentiles() -> None:
    """A one-element distribution returns that element for every percentile.

    Sabotage: change ``if pct >= 1.0: return sorted_sizes[-1]`` to
    ``return 0`` — the p99 assert fires.
    """
    stats = compute_stats({"markdown": [42]})
    assert stats[0].p50 == 42
    assert stats[0].p95 == 42
    assert stats[0].p99 == 42


# ---------------------------------------------------------------------------
# render_human
# ---------------------------------------------------------------------------


def test_render_human_emits_one_line_per_type() -> None:
    """One fixed-column line per type plus a trailing newline.

    Sabotage: switch the row format string to drop ``n=`` — the assert
    on the substring fires.
    """
    out = render_human(
        [
            ChunkStats(source_type="markdown", n=243, mean=482.0, p50=512, p95=720, p99=812),
            ChunkStats(source_type="pptx", n=156, mean=187.0, p50=160, p95=380, p99=520),
        ]
    )
    lines = out.strip().split("\n")
    assert len(lines) == 2
    assert "markdown" in lines[0]
    assert "n=243" in lines[0]
    assert "pptx" in lines[1]
    assert "p99=520" in lines[1]


def test_render_human_empty_returns_actionable_message() -> None:
    """Zero rows → fix:/next: action markers per F21.

    Sabotage: change the empty-state message to ``"empty"`` — the
    assert on ``fix:`` fires.
    """
    out = render_human([])
    assert "fix:" in out
    assert "next:" in out


# ---------------------------------------------------------------------------
# emit_chunk_stats (the public CLI entry point)
# ---------------------------------------------------------------------------


def test_emit_chunk_stats_missing_db_returns_exit_1(tmp_path: Path) -> None:
    """Missing DB returns 1 with an actionable error envelope.

    Sabotage: change the missing-DB return to 0 — the assert fires.
    """
    sink = io.StringIO()
    rc = emit_chunk_stats(tmp_path / "absent.sqlite", sink)
    assert rc == 1
    assert "fix:" in sink.getvalue()


def test_emit_chunk_stats_happy_path(tmp_path: Path) -> None:
    """The end-to-end happy path: seed → emit → exit 0 with table.

    Sabotage: change ``out_sink.write(render_human(stats))`` to
    ``out_sink.write("")`` — the assert that "markdown" appears fires.
    """
    db_path = tmp_path / "k.sqlite"
    _seed_db_via_paths(
        db_path,
        [
            ("per-type-fixtures/markdown/note.md", "per-type-fixtures/markdown", 200, 2),
            ("per-type-fixtures/calendar/event.ics", "per-type-fixtures/calendar", 100, 1),
        ],
    )
    sink = io.StringIO()
    rc = emit_chunk_stats(db_path, sink)
    assert rc == 0
    out = sink.getvalue()
    assert "markdown" in out
    assert "calendar" in out


def test_emit_chunk_stats_empty_corpus_emits_empty_message(tmp_path: Path) -> None:
    """A schema-only DB with no documents prints the no-chunks message.

    Sabotage: drop the empty-state branch in ``render_human`` — the
    assert on ``fix:`` fires for the empty case.
    """
    db_path = tmp_path / "k.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.commit()
    db.close()
    sink = io.StringIO()
    rc = emit_chunk_stats(db_path, sink)
    assert rc == 0
    assert "fix:" in sink.getvalue()


# ---------------------------------------------------------------------------
# ChunkStats dataclass shape (smoke)
# ---------------------------------------------------------------------------


def test_chunk_stats_is_frozen_dataclass() -> None:
    """ChunkStats is the documented frozen dataclass.

    Sabotage: remove ``frozen=True`` — the mutation no longer raises
    FrozenInstanceError.
    """
    from dataclasses import FrozenInstanceError

    s = ChunkStats(source_type="markdown", n=10, mean=100.0, p50=80, p95=140, p99=160)
    with pytest.raises(FrozenInstanceError):
        s.n = 99  # type: ignore[misc]  # F3 — frozen dataclass; the test deliberately mutates to prove the assertion
