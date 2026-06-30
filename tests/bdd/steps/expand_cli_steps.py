"""Step definitions for cli_expand.feature.

F46-clean: every scenario composes through the public CLI surface
(``kairix.use_cases.expand.main``) with deps injected through the public
seam — the canonical ``FakeDocumentRepository.get_by_path`` chunk reader.
No direct pipeline construction, no monkeypatching (F1), no env vars (F2).
F13-clean: scenarios speak in agent/document language, never implementation
symbols.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.use_cases.expand import ExpandDeps
from kairix.use_cases.expand import main as expand_main
from tests.fakes import FakeDocumentRepository

pytestmark = pytest.mark.bdd

_URI = "m365://doc-alpha"
# 10 words per chunk -> 13 estimated tokens each (int(10 * 1.3)).
_NINE_WORDS = "alpha beta gamma delta epsilon zeta eta theta iota"


@dataclass
class _ExpandState:
    """Per-scenario state — fresh on every scenario."""

    chunk_count: int = 0
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    envelope: dict[str, Any] = field(default_factory=dict)


@pytest.fixture
def _expand_state() -> _ExpandState:
    return _ExpandState()


def _deps_for(state: _ExpandState) -> ExpandDeps:
    documents = [
        {
            "path": f"{_URI}#{seq}",
            "title": "Alpha Doc",
            "collection": "team-notes",
            "content": f"{_NINE_WORDS} seq{seq}",
        }
        for seq in range(state.chunk_count)
    ]
    repo = FakeDocumentRepository(documents=documents)
    return ExpandDeps(get_chunk=repo.get_by_path)


def _run(state: _ExpandState, seq: int, budget: int) -> None:
    out, err = io.StringIO(), io.StringIO()
    state.exit_code = expand_main(
        [_URI, str(seq), "--token-budget", str(budget), "--json"],
        deps=_deps_for(state),
        out=out,
        err=err,
    )
    state.stdout = out.getvalue()
    state.stderr = err.getvalue()
    state.envelope = json.loads(state.stdout) if state.stdout.strip() else {}


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse("a document indexed as {count:d} chunks"))
def _document_indexed(_expand_state: _ExpandState, count: int) -> None:
    _expand_state.chunk_count = count


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when(parsers.parse("the agent expands the hit at chunk {seq:d} with a generous budget"))
def _expand_generous(_expand_state: _ExpandState, seq: int) -> None:
    _run(_expand_state, seq, 10_000)


@when(parsers.parse("the agent expands the hit at chunk {seq:d} with a budget for one chunk"))
def _expand_tight(_expand_state: _ExpandState, seq: int) -> None:
    _run(_expand_state, seq, 13)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("the response includes the matched chunk and both of its neighbours")
def _includes_neighbours(_expand_state: _ExpandState) -> None:
    seqs = [c["seq"] for c in _expand_state.envelope["chunks"]]
    # The matched chunk plus its immediate preceding + following chunks are
    # all present, returned in ascending document order.
    assert {1, 2, 3}.issubset(set(seqs)), f"expected 1,2,3 present; got {seqs!r}"
    assert seqs == sorted(seqs), f"expected ascending document order; got {seqs!r}"


@then("the matched chunk is flagged as the match")
def _match_flagged(_expand_state: _ExpandState) -> None:
    matches = [c["seq"] for c in _expand_state.envelope["chunks"] if c["is_match"]]
    assert matches == [2], f"expected exactly chunk 2 flagged; got {matches!r}"


@then("the expand response reports no error")
def _no_error(_expand_state: _ExpandState) -> None:
    assert _expand_state.exit_code == 0, f"stderr: {_expand_state.stderr!r}"
    assert _expand_state.envelope["error"] == ""


@then("the response includes only the matched chunk")
def _only_match(_expand_state: _ExpandState) -> None:
    seqs = [c["seq"] for c in _expand_state.envelope["chunks"]]
    assert seqs == [2], f"expected only the matched chunk; got {seqs!r}"


@then("the expand response says no chunk is stored there")
def _says_missing(_expand_state: _ExpandState) -> None:
    assert _expand_state.exit_code == 1
    assert "no chunk stored" in _expand_state.envelope["error"]
