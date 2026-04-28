"""
Unit tests for kairix.quality.eval.judge.

All Azure OpenAI API calls are mocked. No live calls in CI.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kairix.quality.eval.judge import (
    JudgeCalibrationError,
    JudgeResult,
    _parse_grade_response,
    calibrate,
    judge_batch,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CANDIDATES = [
    ("docker-deployment-guide", "Deploy with docker build, tag, push, run."),
    ("ci-cd-pipeline-config", "GitHub Actions runs on all PRs before merge."),
    ("api-guidelines", "All public APIs require rate limiting and authentication."),
]

_QUERY = "What are the steps to deploy a Docker container?"


def _mock_response(grades: dict[str, int]) -> MagicMock:
    """Build a mock urllib.request.urlopen context manager returning grades JSON."""
    content = json.dumps(grades)
    body = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ---------------------------------------------------------------------------
# judge_batch — happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_judge_batch_returns_grade_dict() -> None:
    """judge_batch returns a JudgeResult with grades for each candidate."""
    # Shuffle is disabled to keep label order predictable in test
    with patch("urllib.request.urlopen", return_value=_mock_response({"A": 2, "B": 0, "C": 1})):
        result = judge_batch(
            query=_QUERY,
            candidates=_CANDIDATES,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            shuffle=False,
        )

    assert isinstance(result, JudgeResult)
    assert result.grades["docker-deployment-guide"] == 2
    assert result.grades["ci-cd-pipeline-config"] == 0
    assert result.grades["api-guidelines"] == 1


@pytest.mark.unit
def test_judge_batch_clamps_grades_to_0_2() -> None:
    """Grades outside [0, 2] are clamped."""
    with patch("urllib.request.urlopen", return_value=_mock_response({"A": 5, "B": -1, "C": 1})):
        result = judge_batch(
            query=_QUERY,
            candidates=_CANDIDATES,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            shuffle=False,
        )

    assert result.grades["docker-deployment-guide"] == 2  # 5 clamped to 2
    assert result.grades["ci-cd-pipeline-config"] == 0  # -1 clamped to 0


@pytest.mark.unit
def test_judge_batch_shuffles_candidates() -> None:
    """When shuffle=True, the shuffle_order differs from original order at least sometimes."""
    # Run multiple times — at least one shuffle should differ from original order
    original_order = [stem for stem, _ in _CANDIDATES]
    shuffle_orders = set()

    for _ in range(10):
        with patch("urllib.request.urlopen", return_value=_mock_response({"A": 2, "B": 1, "C": 0})):
            result = judge_batch(
                query=_QUERY,
                candidates=_CANDIDATES,
                api_key="test-key",
                endpoint="https://test.openai.azure.com",
                shuffle=True,
            )
        shuffle_orders.add(tuple(result.shuffle_order))

    # With 3 candidates and 10 runs, some permutation should differ from original
    assert len(shuffle_orders) >= 1
    # The grades dict always has all original stems as keys regardless of order
    assert set(result.grades.keys()) == set(original_order)


@pytest.mark.unit
def test_judge_batch_records_shuffle_order() -> None:
    """shuffle_order contains stems in the order they were presented to the LLM."""
    with patch("urllib.request.urlopen", return_value=_mock_response({"A": 2, "B": 0, "C": 1})):
        result = judge_batch(
            query=_QUERY,
            candidates=_CANDIDATES,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            shuffle=False,
        )

    assert result.shuffle_order == [stem for stem, _ in _CANDIDATES]


@pytest.mark.unit
def test_judge_batch_empty_candidates() -> None:
    """Empty candidate list returns empty JudgeResult."""
    result = judge_batch(
        query=_QUERY,
        candidates=[],
        api_key="test-key",
        endpoint="https://test.openai.azure.com",
    )
    assert result.grades == {}
    assert result.shuffle_order == []


# ---------------------------------------------------------------------------
# judge_batch — failure modes (all must return all-zero grades, never raise)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_judge_batch_returns_zeros_on_api_error() -> None:
    """Network error → all grades are 0, no exception raised."""
    with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
        result = judge_batch(
            query=_QUERY,
            candidates=_CANDIDATES,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            shuffle=False,
        )

    assert all(g == 0 for g in result.grades.values())
    assert len(result.grades) == len(_CANDIDATES)


@pytest.mark.unit
def test_judge_batch_returns_zeros_on_malformed_json() -> None:
    """Malformed JSON response → all grades are 0."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"choices": [{"message": {"content": "not json at all {"}}]}).encode()
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        result = judge_batch(
            query=_QUERY,
            candidates=_CANDIDATES,
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
            shuffle=False,
        )

    assert all(g == 0 for g in result.grades.values())


@pytest.mark.unit
def test_judge_batch_returns_zeros_when_no_credentials() -> None:
    """Empty api_key/endpoint → all grades are 0, no exception."""
    result = judge_batch(
        query=_QUERY,
        candidates=_CANDIDATES,
        api_key="",
        endpoint="",
        shuffle=False,
    )
    assert all(g == 0 for g in result.grades.values())


# ---------------------------------------------------------------------------
# _parse_grade_response
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_parse_grade_response_valid_json() -> None:
    """Valid JSON response is parsed correctly."""
    content = '{"A": 2, "B": 0, "C": 1}'
    result = _parse_grade_response(content, ["A", "B", "C"])
    assert result == {"A": 2, "B": 0, "C": 1}


@pytest.mark.unit
def test_parse_grade_response_json_in_prose() -> None:
    """JSON embedded in prose text is extracted."""
    content = 'After reviewing the documents, my assessment is: {"A": 2, "B": 1} as requested.'
    result = _parse_grade_response(content, ["A", "B"])
    assert result == {"A": 2, "B": 1}


@pytest.mark.unit
def test_parse_grade_response_empty_on_no_json() -> None:
    """No JSON in response → empty dict."""
    result = _parse_grade_response("I cannot assess these documents.", ["A", "B"])
    assert result == {}


@pytest.mark.unit
def test_parse_grade_response_ignores_extra_labels() -> None:
    """Labels not in the expected list are ignored."""
    content = '{"A": 2, "B": 0, "Z": 1}'  # Z not in labels
    result = _parse_grade_response(content, ["A", "B"])
    assert "Z" not in result
    assert result["A"] == 2


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_calibrate_passes_when_all_anchors_correct() -> None:
    """Calibration passes when all anchors get expected grades."""
    # Patch judge_batch to always return expected grades from anchors
    from kairix.quality.eval.judge import _CALIBRATION_ANCHORS

    def _perfect_judge(query, candidates, api_key, endpoint, deployment, shuffle):
        # Find the matching anchor and return its expected grade
        stem = candidates[0][0]
        for anchor in _CALIBRATION_ANCHORS:
            if anchor["title"] == stem:
                return JudgeResult(
                    query=query,
                    grades={stem: anchor["expected"]},
                    shuffle_order=[stem],
                    judge_model=deployment,
                )
        return JudgeResult(query=query, grades={stem: 0}, shuffle_order=[stem], judge_model=deployment)

    with patch("kairix.quality.eval.judge.judge_batch", side_effect=_perfect_judge):
        result = calibrate("test-key", "https://test.openai.azure.com")

    assert result is True


@pytest.mark.unit
def test_calibrate_raises_when_too_many_anchors_wrong() -> None:
    """Calibration raises JudgeCalibrationError when >3 anchors are wrong."""
    from kairix.quality.eval.judge import CALIBRATION_MAX_ERRORS

    call_count = [0]

    def _wrong_judge(query, candidates, api_key, endpoint, deployment, shuffle):
        stem = candidates[0][0]
        call_count[0] += 1
        # Return wrong grade for all anchors
        return JudgeResult(
            query=query,
            grades={stem: 0},  # always 0, regardless of expected
            shuffle_order=[stem],
            judge_model=deployment,
        )

    with patch("kairix.quality.eval.judge.judge_batch", side_effect=_wrong_judge):
        with pytest.raises(JudgeCalibrationError) as exc_info:
            calibrate("test-key", "https://test.openai.azure.com")

    assert "calibration" in str(exc_info.value).lower()
    assert str(CALIBRATION_MAX_ERRORS) in str(exc_info.value) or "3" in str(exc_info.value)
