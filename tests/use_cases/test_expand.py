"""Unit tests for the chunk-expansion use case (PLA-268).

The neighbour-walking + token-budget logic is the property under test. The
by-key chunk-retrieval seam is injected as the canonical
``FakeDocumentRepository.get_by_path`` (the seam is a ``Callable[[str], dict |
None]`` — exactly that fake's method shape), so no SQLite is touched and the
walk is exercised in isolation.
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterable
from pathlib import Path

import pytest

from kairix.core.db.schema import create_schema
from kairix.core.protocols import SourceRef
from kairix.use_cases.expand import (
    ExpandDeps,
    ExpandedChunk,
    ExpandOutput,
    expand_output_to_envelope,
    main,
    run_expand,
)
from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.unit

_URI = "m365://doc-alpha"
# Each chunk is exactly 10 words (9 fixed + 1 seq-tagged) so
# estimate_tokens == int(10 * 1.3) == 13 tokens per chunk, distinct text.
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


def _deps_for_chunks(source_uri: str, count: int) -> ExpandDeps:
    """Build deps backed by a FakeDocumentRepository seeded with ``count`` chunks."""
    return _deps_for_seqs(source_uri, range(count))


def _deps_for_seqs(
    source_uri: str,
    seqs: Iterable[int],
    *,
    doc_level_content: str | None = None,
) -> ExpandDeps:
    """Build deps seeded with chunk rows at the given ``seqs``.

    ``seqs`` may start above 0 so the source_uri-only anchor selection (which
    picks ``min(seqs)``) is observable. ``doc_level_content`` seeds an extra
    row keyed by the BARE ``source_uri`` (no ``#seq``) for the doc-level-only
    fallback. Both the by-key and the by-prefix seams are wired to the same
    fake so source_uri-only mode works end to end.
    """
    documents = [
        {
            "path": f"{source_uri}#{seq}",
            "title": "Alpha Doc",
            "collection": "team-notes",
            "content": f"{_NINE_WORDS} seq{seq}",
        }
        for seq in seqs
    ]
    if doc_level_content is not None:
        documents.append(
            {
                "path": source_uri,
                "title": "Alpha Doc",
                "collection": "team-notes",
                "content": doc_level_content,
            }
        )
    repo = FakeDocumentRepository(documents=documents)
    return ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs)


def test_returns_matched_plus_neighbours_ordered_by_seq() -> None:
    out = run_expand(_URI, 2, token_budget=10_000, deps=_deps_for_chunks(_URI, 5))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [0, 1, 2, 3, 4]
    # Exactly one match marker, on the requested seq.
    matches = [c.seq for c in out.chunks if c.is_match]
    assert matches == [2]


def test_token_budget_caps_the_window_to_a_centred_subset() -> None:
    # match(13) + seq1(13) + seq3(13) = 39 == budget; seq3 fills it EXACTLY
    # (pins ``> budget``: ``>= budget`` would drop seq3). seq0/seq4 would push to 52.
    out = run_expand(_URI, 2, token_budget=39, deps=_deps_for_chunks(_URI, 5))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [1, 2, 3]
    assert out.total_tokens == 39


def test_token_budget_below_boundary_drops_the_chunk_that_overflows() -> None:
    # One token under the 39 the centred [1,2,3] window needs: seq3 overflows
    # (26 + 13 = 39 > 38) so the window narrows to [1, 2].
    out = run_expand(_URI, 2, token_budget=38, deps=_deps_for_chunks(_URI, 5))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [1, 2]
    assert out.total_tokens == 26


def test_tiny_budget_returns_only_the_matched_chunk() -> None:
    out = run_expand(_URI, 2, token_budget=13, deps=_deps_for_chunks(_URI, 5))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [2]
    assert out.chunks[0].is_match is True


def test_seq_zero_has_no_preceding_chunk() -> None:
    out = run_expand(_URI, 0, token_budget=10_000, deps=_deps_for_chunks(_URI, 3))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [0, 1, 2]
    # The match is the first chunk — no negative-seq lookup was attempted.
    assert min(c.seq for c in out.chunks) == 0


def test_end_of_document_stops_the_forward_walk() -> None:
    # Only seq 0..2 exist; expanding around the last chunk yields the tail.
    out = run_expand(_URI, 2, token_budget=10_000, deps=_deps_for_chunks(_URI, 3))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [0, 1, 2]


def test_missing_matched_chunk_returns_actionable_error() -> None:
    out = run_expand(_URI, 9, token_budget=10_000, deps=_deps_for_chunks(_URI, 3))
    assert out.chunks == []
    assert "no chunk stored" in out.error
    assert f"{_URI}#9" in out.error


def test_empty_source_uri_is_rejected() -> None:
    out = run_expand("", 0, deps=_deps_for_chunks(_URI, 3))
    assert out.chunks == []
    assert "source_uri is required" in out.error


def test_negative_seq_is_rejected() -> None:
    out = run_expand(_URI, -1, deps=_deps_for_chunks(_URI, 3))
    assert out.chunks == []
    assert "seq must be >= 0" in out.error


def test_source_uri_only_anchors_on_the_first_existing_chunk() -> None:
    """A doc-level (L2) hit with no seq resolves the document's real chunks by
    prefix and anchors on the FIRST existing chunk — not a guessed ``#0``.

    The chunks deliberately start at seq 5 (no 0..4 exist), so an
    anchor-selection bug is observable: the CHOSEN anchor must be 5, the
    window must be the ordered [5, 6, 7], and the match marker must sit on 5.
    """
    out = run_expand(_URI, None, token_budget=10_000, deps=_deps_for_seqs(_URI, [5, 6, 7]))
    assert out.error == ""
    assert out.no_finer_chunks is False
    assert [c.seq for c in out.chunks] == [5, 6, 7]
    assert out.matched_seq == 5
    # Exactly the anchor chunk is flagged — pins the CHOSEN chunk, not merely
    # a non-empty window.
    assert [c.seq for c in out.chunks if c.is_match] == [5]


def test_source_uri_only_returns_ordered_window_from_document_start() -> None:
    """Chunks 0..4 present, no seq passed → the window anchors on chunk 0 and
    returns the ordered neighbour window."""
    out = run_expand(_URI, None, token_budget=10_000, deps=_deps_for_seqs(_URI, range(5)))
    assert out.error == ""
    assert [c.seq for c in out.chunks] == [0, 1, 2, 3, 4]
    assert [c.seq for c in out.chunks if c.is_match] == [0]
    assert out.matched_seq == 0


def test_doc_level_only_returns_content_with_no_finer_chunks_signal() -> None:
    """No finer ``#seq`` chunk rows exist, only a bare doc-level row → expand
    returns the whole-document content with the explicit no-finer-chunks
    signal, never a second dead-end (PLA-297 / cf. #627)."""
    deps = _deps_for_seqs(_URI, [], doc_level_content="the whole document body")
    out = run_expand(_URI, None, token_budget=10_000, deps=deps)
    assert out.error == ""
    assert out.no_finer_chunks is True
    assert len(out.chunks) == 1
    assert out.chunks[0].text == "the whole document body"
    assert out.chunks[0].source_ref().source_uri == _URI
    # The whole-document row is flagged as the match (pins ``is_match=True``).
    assert out.chunks[0].is_match is True
    # No chunk seq to point at — matched_seq is None for a whole-doc return.
    assert out.matched_seq is None


def test_output_from_envelope_defaults_no_finer_chunks_false_when_key_absent() -> None:
    """An envelope with no ``no_finer_chunks`` key rebuilds as ``False`` (an
    ordinary chunk window) — pins the ``False`` default (a ``True`` default
    would mis-flag every legacy envelope as doc-level-only)."""
    rebuilt = ExpandOutput.from_envelope({"source_uri": _URI, "matched_seq": 1, "chunks": []})
    assert rebuilt.no_finer_chunks is False


def test_source_uri_only_anchor_row_missing_falls_back_to_doc_level() -> None:
    """A race: list_chunk_seqs reports seq 5 but the anchor chunk row has
    vanished — expand falls back to the doc-level content instead of a
    dead-end (pins the ``if out.chunks`` fallthrough)."""

    def _list(_uri: str) -> list[int]:
        return [5]  # claims a chunk exists...

    def _get(path: str) -> dict[str, object] | None:
        # ...but only the bare doc-level row resolves; the #5 chunk is gone.
        if path == _URI:
            return {"path": _URI, "content": "doc body", "collection": "c", "title": "t"}
        return None

    out = run_expand(_URI, None, token_budget=10_000, deps=ExpandDeps(get_chunk=_get, list_chunk_seqs=_list))
    assert out.error == ""
    assert out.no_finer_chunks is True
    assert out.chunks[0].text == "doc body"


def test_cli_text_mode_renders_no_finer_chunks_notice() -> None:
    """Doc-level-only content in text mode surfaces the no-finer-chunks
    notice (pins the ``_format_text`` no_finer_chunks branch)."""
    deps = _deps_for_seqs(_URI, [], doc_level_content="the whole document body")
    out = io.StringIO()

    exit_code = main([_URI], deps=deps, out=out)

    rendered = out.getvalue()
    assert exit_code == 0
    assert "no finer chunks" in rendered
    assert "the whole document body" in rendered


def test_source_uri_only_nothing_stored_is_actionable_not_a_dead_end() -> None:
    """Nothing stored at all for the source_uri → an actionable miss carrying
    the no-finer-chunks signal (not a bare ``#0`` dead-end)."""
    out = run_expand(_URI, None, token_budget=10_000, deps=_deps_for_seqs(_URI, []))
    assert out.chunks == []
    assert out.no_finer_chunks is True
    assert "nothing stored" in out.error
    assert _URI in out.error


def test_cli_source_uri_only_omits_seq_and_renders_window(tmp_path: Path) -> None:
    """The CLI accepts source_uri with NO positional seq (a doc-level hit) and
    renders the resolved neighbour window from the real ``--db-path`` index."""
    db_path = tmp_path / "index.sqlite"
    _seed_index(db_path, chunks=3)
    out = io.StringIO()

    exit_code = main([_URI, "--db-path", str(db_path)], out=out)

    rendered = out.getvalue()
    assert exit_code == 0
    assert "(match)" in rendered
    assert "seq0" in rendered
    assert f"{_URI}#0" in rendered


def test_never_raises_when_the_backbone_raises() -> None:
    def _boom(_path: str) -> dict[str, object] | None:
        raise RuntimeError("db exploded")

    out = run_expand(_URI, 1, deps=ExpandDeps(get_chunk=_boom))
    assert out.chunks == []
    assert out.error.startswith("RuntimeError:")


def test_expanded_chunk_carries_source_ref_breadcrumb() -> None:
    out = run_expand(_URI, 1, token_budget=10_000, deps=_deps_for_chunks(_URI, 3))
    ref = out.chunks[0].source_ref()
    assert isinstance(ref, SourceRef)
    # source_uri is the canonical resolvable breadcrumb for every row.
    assert ref.source_uri == _URI
    assert ref.collection == "team-notes"
    # The document title rides through onto the breadcrumb (pins ``title or None``).
    assert ref.title == "Alpha Doc"


def test_envelope_round_trip_is_lossless() -> None:
    out = run_expand(_URI, 1, token_budget=10_000, deps=_deps_for_chunks(_URI, 3))
    envelope = expand_output_to_envelope(out)
    assert set(envelope.keys()) == {
        "source_uri",
        "matched_seq",
        "chunks",
        "total_tokens",
        "no_finer_chunks",
        "error",
    }
    rebuilt = ExpandOutput.from_envelope(envelope)
    assert rebuilt.source_uri == out.source_uri
    assert rebuilt.matched_seq == out.matched_seq
    assert rebuilt.total_tokens == out.total_tokens
    # Every per-chunk field round-trips byte-for-byte — pins each
    # ``ExpandedChunk.from_envelope`` field reconstruction (no field may
    # collapse to a default through the envelope).
    assert len(rebuilt.chunks) == len(out.chunks) == 3
    for got, want in zip(rebuilt.chunks, out.chunks, strict=True):
        assert got.path == want.path
        assert got.seq == want.seq
        assert got.text == want.text != ""
        assert got.tokens == want.tokens > 0
        assert got.title == want.title == "Alpha Doc"
        assert got.collection == want.collection == "team-notes"
        assert got.source_uri == want.source_uri == _URI
        assert got.is_match == want.is_match
    # The matched chunk's flag survives the round-trip distinctly.
    assert [c.seq for c in rebuilt.chunks if c.is_match] == [1]


def test_chunk_envelope_embeds_resolvable_source_ref() -> None:
    chunk = ExpandedChunk(path=f"{_URI}#0", seq=0, text="hi", tokens=1, source_uri=_URI)
    envelope = chunk.to_envelope()
    assert envelope["source_ref"]["source_uri"] == _URI
    assert envelope["is_match"] is False


def test_from_envelope_defaults_is_match_false_when_key_absent() -> None:
    """A per-chunk dict with no ``is_match`` key rebuilds as a non-match —
    pins the ``False`` default (a ``True`` default would mis-flag neighbours)."""
    rebuilt = ExpandedChunk.from_envelope({"path": f"{_URI}#0", "seq": 0, "text": "x"})
    assert rebuilt.is_match is False


def test_chunk_path_prefers_the_stored_row_path_over_the_computed_key() -> None:
    """The chunk's path comes from the stored row, falling back to the
    computed ``<uri>#<seq>`` only when the row has none — pins the fallback
    ``or`` (an ``and`` would discard the real stored path)."""

    def _get(path: str) -> dict[str, object] | None:
        # Only the matched key resolves; the stored path deliberately differs
        # from the computed ``<uri>#<seq>`` key so the fallback ``or`` is testable.
        if path == f"{_URI}#0":
            return {"path": f"STORED::{path}", "content": "body text here", "collection": "c", "title": "t"}
        return None

    out = run_expand(_URI, 0, token_budget=10_000, deps=ExpandDeps(get_chunk=_get))
    assert out.error == ""
    assert out.chunks[0].path == f"STORED::{_URI}#0"


def _seed_index(db_path: Path, *, chunks: int) -> None:
    """Write ``chunks`` chunk rows for ``_URI`` into a real on-disk index."""
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        create_schema(db)
        for seq in range(chunks):
            chunk_hash = f"hash-{seq}"
            db.execute(
                "INSERT INTO documents (collection, path, hash, source_uri, sensitivity, active) "
                "VALUES (?, ?, ?, ?, 'public', 1)",
                ("team-notes", f"{_URI}#{seq}", chunk_hash, _URI),
            )
            db.execute("INSERT INTO content (hash, doc) VALUES (?, ?)", (chunk_hash, f"{_NINE_WORDS} seq{seq}"))
        db.commit()
    finally:
        db.close()


def test_cli_text_mode_renders_chunks_via_db_path(tmp_path: Path) -> None:
    """The ``--db-path`` seam wires the real repository and text mode renders
    the window — exercises ``main`` + ``_deps_from_args`` + ``_format_text``."""
    db_path = tmp_path / "index.sqlite"
    _seed_index(db_path, chunks=3)
    out = io.StringIO()

    exit_code = main([_URI, "1", "--db-path", str(db_path)], out=out)

    rendered = out.getvalue()
    assert exit_code == 0
    assert "(match)" in rendered
    assert "seq1" in rendered
    assert f"{_URI}#1" in rendered


def test_cli_text_mode_renders_error_to_stderr(tmp_path: Path) -> None:
    """A miss in text mode writes the actionable error to stderr + exits 1."""
    db_path = tmp_path / "index.sqlite"
    _seed_index(db_path, chunks=2)
    out, err = io.StringIO(), io.StringIO()

    exit_code = main([_URI, "9", "--db-path", str(db_path)], out=out, err=err)

    assert exit_code == 1
    assert "no chunk stored" in err.getvalue()
