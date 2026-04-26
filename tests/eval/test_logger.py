"""Unit tests for kairix.eval.logger.QueryLogger."""

import json

import pytest

from kairix.eval.logger import QueryLogger
from kairix.eval.schema import QueryLogEntry


@pytest.mark.unit
def test_logger_writes_jsonl(tmp_path):
    log_path = tmp_path / "test.jsonl"
    ql = QueryLogger(log_path=log_path)
    entry = QueryLogEntry(
        ts="2026-04-16T10:00:00Z",
        agent="shape",
        query="test query",
        intent="semantic",
        result_count=5,
        bm25_count=3,
        vec_count=2,
        latency_ms=42.0,
    )
    ql.log(entry)
    assert log_path.exists()
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["query"] == "test query"
    assert row["agent"] == "shape"


@pytest.mark.unit
def test_logger_appends(tmp_path):
    log_path = tmp_path / "test.jsonl"
    ql = QueryLogger(log_path=log_path)
    for i in range(3):
        ql.log(
            QueryLogEntry(
                ts="2026-04-16T10:00:00Z",
                agent="shape",
                query=f"query {i}",
                intent="semantic",
                result_count=1,
                bm25_count=1,
                vec_count=0,
                latency_ms=10.0,
            )
        )
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 3


@pytest.mark.unit
def test_logger_never_raises_on_bad_path():
    """Logger must not raise even if the path is unwritable."""
    ql = QueryLogger(log_path="/nonexistent/deep/path/test.jsonl")
    entry = QueryLogEntry(
        ts="2026-04-16T10:00:00Z",
        agent="shape",
        query="q",
        intent="semantic",
        result_count=0,
        bm25_count=0,
        vec_count=0,
        latency_ms=0.0,
    )
    ql.log(entry)  # should not raise
    assert True, "smoke: unwritable path did not raise"
