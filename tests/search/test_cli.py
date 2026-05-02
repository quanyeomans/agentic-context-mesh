"""Tests for kairix.core.search.cli — search CLI entry point.

Covers:
  - _parse_args: argument parsing for query, flags, and defaults
  - _format_result: human-readable output formatting
  - main: end-to-end with injected pipeline (no monkey-patching)
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from kairix.core.search.cli import _format_result, _parse_args, main
from kairix.core.search.intent import QueryIntent
from kairix.core.search.pipeline import SearchResult

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _parse_args
# ---------------------------------------------------------------------------


def test_parse_args_query_required() -> None:
    """Query argument is positional and required."""
    args = _parse_args(["hello world"])
    assert args.query == "hello world"


def test_parse_args_defaults() -> None:
    """Default values for optional flags."""
    args = _parse_args(["q"])
    assert args.agent is None
    assert args.scope == "shared+agent"
    assert args.budget == 3000
    assert args.limit == 10
    assert args.as_json is False


def test_parse_args_all_flags() -> None:
    """All flags are parsed correctly."""
    args = _parse_args(
        [
            "q",
            "--agent",
            "builder",
            "--scope",
            "agent",
            "--budget",
            "500",
            "--limit",
            "5",
            "--json",
        ]
    )
    assert args.agent == "builder"
    assert args.scope == "agent"
    assert args.budget == 500
    assert args.limit == 5
    assert args.as_json is True


# ---------------------------------------------------------------------------
# _format_result
# ---------------------------------------------------------------------------


def test_format_result_no_results() -> None:
    """Formats correctly when there are no results."""
    sr = SearchResult(query="test", intent=QueryIntent.SEMANTIC)
    output = _format_result(sr, limit=10)
    assert "No results found" in output
    assert "test" in output


def test_format_result_with_error() -> None:
    """Error is displayed when present."""
    sr = SearchResult(query="test", intent=QueryIntent.SEMANTIC, error="something broke")
    output = _format_result(sr, limit=10)
    assert "Error: something broke" in output


# ---------------------------------------------------------------------------
# Fake pipeline for CLI integration tests
# ---------------------------------------------------------------------------


@dataclass
class _FakeBudgetedResult:
    """Minimal budgeted result for CLI output tests."""

    result: _FakeResult
    tier: str = "T1"
    content: str = "snippet content"


@dataclass
class _FakeResult:
    path: str = "docs/test.md"
    title: str = "Test Doc"
    snippet: str = "A test snippet"
    boosted_score: float = 0.95
    collection: str = "shared"


class _FakePipeline:
    """Fake search pipeline that returns a fixed SearchResult."""

    def __init__(self, result: SearchResult | None = None) -> None:
        self._result = result or SearchResult(
            query="test",
            intent=QueryIntent.SEMANTIC,
            results=[_FakeBudgetedResult(result=_FakeResult())],
            bm25_count=1,
            vec_count=1,
            fused_count=1,
            total_tokens=100,
            latency_ms=5.0,
        )
        self.last_call: dict | None = None

    def search(self, *, query: str, budget: int, scope: str, agent: str | None) -> SearchResult:
        self.last_call = {
            "query": query,
            "budget": budget,
            "scope": scope,
            "agent": agent,
        }
        self._result.query = query
        return self._result


# ---------------------------------------------------------------------------
# main() — end-to-end with injected pipeline
# ---------------------------------------------------------------------------


def test_main_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints human-readable output by default."""
    pipeline = _FakePipeline()
    main(["my query"], pipeline=pipeline)
    captured = capsys.readouterr()
    assert "my query" in captured.out
    assert pipeline.last_call is not None
    assert pipeline.last_call["query"] == "my query"


def test_main_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints JSON when --json flag is set."""
    import json

    pipeline = _FakePipeline()
    main(["my query", "--json"], pipeline=pipeline)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["query"] == "my query"
    assert "results" in data


def test_main_passes_flags_to_pipeline() -> None:
    """main() passes CLI flags through to the pipeline."""
    pipeline = _FakePipeline()
    main(
        ["q", "--agent", "builder", "--scope", "agent", "--budget", "500"],
        pipeline=pipeline,
    )
    assert pipeline.last_call is not None
    assert pipeline.last_call["agent"] == "builder"
    assert pipeline.last_call["scope"] == "agent"
    assert pipeline.last_call["budget"] == 500


def test_main_exits_on_error(capsys: pytest.CaptureFixture[str]) -> None:
    """main() exits with code 1 when SearchResult has an error."""
    error_result = SearchResult(
        query="test",
        intent=QueryIntent.SEMANTIC,
        error="search failed",
    )
    pipeline = _FakePipeline(result=error_result)
    with pytest.raises(SystemExit) as exc_info:
        main(["test"], pipeline=pipeline)
    assert exc_info.value.code == 1
