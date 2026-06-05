"""
Unit tests for kairix.quality.eval.judge.

All Azure OpenAI API calls are injected via ``chat_backend=FakeChatBackend(...)``.
No monkey-patching, no @patch, no setattr. The new ChatBackend protocol replaces
the legacy ``chat_fn=`` substitution kwarg (#143 Phase 2a).
"""

from __future__ import annotations

import json

import pytest

from kairix.quality.eval.judge import (
    CALIBRATION_ANCHORS,
    JUDGE_DEPLOYMENT,
    JudgeCalibrationError,
    JudgeResult,
    LLMJudge,
    fetch_llm_credentials,
)
from tests.fakes import FakeChatBackend


def _grade_with(
    backend: FakeChatBackend,
    *,
    query: str,
    candidates: list[tuple[str, str]],
    api_key: str = "test-key",
    endpoint: str = "https://test.openai.azure.com",
    deployment: str = JUDGE_DEPLOYMENT,
    shuffle: bool = False,
) -> JudgeResult:
    """Test-scoped helper: build an LLMJudge from ``backend`` and call ``grade``.

    The legacy ``judge_batch`` free-function shim was retired in v2026.6;
    every parse / failure-mode test now drives the same behaviour through
    the canonical ``LLMJudge.grade`` entrypoint.
    """
    return LLMJudge(chat_backend=backend, deployment=deployment).grade(
        query,
        candidates,
        api_key=api_key,
        endpoint=endpoint,
        shuffle=shuffle,
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


def _grade_response(grades: dict[str, int]) -> str:
    """Return a JSON string of grades, mimicking chat-completion output."""
    return json.dumps(grades)


# ---------------------------------------------------------------------------
# Grade-parsing scenarios driven through ``LLMJudge.grade`` (the surviving
# entrypoint after the v2026.6 ``judge_batch`` shim retirement).
#
# The chat backend's response string is the parser's input; the resulting
# JudgeResult.grades dict is the parser's output as observed by callers.
# Each test exercises a parser shape not covered by the LLMJudge-class
# tests further down (constructor / deployment plumbing / failure modes).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_pure_json_response_grades_each_candidate_per_label() -> None:
    """Pure-JSON {"A": 2, "B": 0, "C": 1} → grades reflect label→candidate mapping."""
    backend = FakeChatBackend(responses=['{"A": 2, "B": 0, "C": 1}'])
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES)
    assert result.grades["docker-deployment-guide"] == 2
    assert result.grades["ci-cd-pipeline-config"] == 0
    assert result.grades["api-guidelines"] == 1


@pytest.mark.unit
def test_json_embedded_in_prose_is_extracted_and_used() -> None:
    """A JSON object inside prose still drives the grades."""
    backend = FakeChatBackend(
        responses=['After reviewing the documents, my assessment is: {"A": 2, "B": 1, "C": 0} as requested.']
    )
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES)
    assert result.grades["docker-deployment-guide"] == 2
    assert result.grades["ci-cd-pipeline-config"] == 1
    assert result.grades["api-guidelines"] == 0


@pytest.mark.unit
def test_response_with_no_json_yields_all_zero_grades() -> None:
    """No JSON object in the response → every candidate gets 0."""
    backend = FakeChatBackend(responses=["I cannot assess these documents."])
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES)
    assert all(g == 0 for g in result.grades.values())


@pytest.mark.unit
def test_extra_labels_in_response_are_ignored() -> None:
    """Labels beyond the candidate count (e.g. ``Z``) are dropped silently."""
    # Two candidates → labels A, B. The response includes a stray Z which must not surface.
    backend = FakeChatBackend(responses=['{"A": 2, "B": 0, "Z": 1}'])
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES[:2])
    assert set(result.grades.keys()) == {"docker-deployment-guide", "ci-cd-pipeline-config"}
    assert result.grades["docker-deployment-guide"] == 2
    assert result.grades["ci-cd-pipeline-config"] == 0


@pytest.mark.unit
def test_invalid_json_inside_braces_yields_all_zero_grades() -> None:
    """Brace block exists but body fails json.loads → all grades 0 (never raises)."""
    backend = FakeChatBackend(responses=["{A: 2, B: 0}"])  # missing quotes
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES[:2])
    assert all(g == 0 for g in result.grades.values())


@pytest.mark.unit
def test_non_int_grade_values_are_clamped_to_zero() -> None:
    """A non-int-coercible value (string/null) becomes 0 for that candidate only."""
    backend = FakeChatBackend(responses=['{"A": "high", "B": 1, "C": null}'])
    result = _grade_with(backend, query=_QUERY, candidates=_CANDIDATES)
    # int("high") raises ValueError → 0; int(None) raises TypeError → 0; int(1) → 1
    assert result.grades["docker-deployment-guide"] == 0
    assert result.grades["ci-cd-pipeline-config"] == 1
    assert result.grades["api-guidelines"] == 0


@pytest.mark.unit
def test_empty_candidates_returns_empty_judge_result() -> None:
    """Empty candidate list returns empty JudgeResult — early-return path."""
    result = _grade_with(
        FakeChatBackend(responses=[]),
        query=_QUERY,
        candidates=[],
    )
    assert result.grades == {}
    assert result.shuffle_order == ()


@pytest.mark.unit
def test_empty_credentials_returns_zeros() -> None:
    """Empty api_key/endpoint → all grades are 0, no exception.

    The empty-credential branch lives inside ``LLMJudge.grade`` (raises
    ``ValueError`` which is caught by the same method's try/except).
    Pass an explicit ``FakeChatBackend`` so we exercise the empty-credential
    branch deterministically without depending on provider resolution.
    """
    result = _grade_with(
        FakeChatBackend(responses=[]),
        query=_QUERY,
        candidates=_CANDIDATES,
        api_key="",
        endpoint="",
    )
    assert all(g == 0 for g in result.grades.values())


# ---------------------------------------------------------------------------
# LLMJudge class — Phase 2a constructor-injected wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_llm_judge_constructor_stores_backend_and_deployment() -> None:
    """LLMJudge stores its dependencies for later delegation."""
    backend = FakeChatBackend(responses=["unused"])
    judge = LLMJudge(chat_backend=backend, deployment="custom-model")

    # Internal attributes are private, but their effects show up via grade()/calibrate();
    # we still assert default deployment behaviour next.
    backend2 = FakeChatBackend(responses=["unused"])
    judge_default = LLMJudge(chat_backend=backend2)

    # Default deployment should match the module constant.
    assert judge_default._deployment == JUDGE_DEPLOYMENT
    assert judge._deployment == "custom-model"


@pytest.mark.unit
def test_llm_judge_grade_returns_judge_result() -> None:
    """LLMJudge.grade() invokes the injected backend and returns a JudgeResult."""
    backend = FakeChatBackend(responses=[_grade_response({"A": 2, "B": 1, "C": 0})])
    judge = LLMJudge(chat_backend=backend)

    result = judge.grade(
        _QUERY,
        _CANDIDATES,
        api_key="test-key",
        endpoint="https://test.openai.azure.com",
        shuffle=False,
    )

    assert isinstance(result, JudgeResult)
    assert result.grades["docker-deployment-guide"] == 2
    assert result.grades["ci-cd-pipeline-config"] == 1
    assert result.grades["api-guidelines"] == 0
    # The backend received exactly one call.
    assert len(backend.calls) == 1
    assert backend.calls[0]["api_key"] == "test-key"
    assert backend.calls[0]["deployment"] == JUDGE_DEPLOYMENT


@pytest.mark.unit
def test_llm_judge_grade_uses_configured_deployment() -> None:
    """LLMJudge passes its configured deployment through to the backend."""
    backend = FakeChatBackend(responses=[_grade_response({"A": 1, "B": 0, "C": 0})])
    judge = LLMJudge(chat_backend=backend, deployment="my-custom-deployment")

    result = judge.grade(
        _QUERY,
        _CANDIDATES,
        api_key="key",  # pragma: allowlist secret
        endpoint="https://endpoint",
        shuffle=False,
    )

    assert result.judge_model == "my-custom-deployment"
    assert backend.calls[0]["deployment"] == "my-custom-deployment"


@pytest.mark.unit
def test_llm_judge_grade_returns_zeros_on_backend_error() -> None:
    """LLMJudge.grade() never raises — returns all-zero grades on backend error."""
    backend = FakeChatBackend(raise_on_call=OSError("rate limit"))
    judge = LLMJudge(chat_backend=backend)

    result = judge.grade(
        _QUERY,
        _CANDIDATES,
        api_key="key",  # pragma: allowlist secret
        endpoint="https://endpoint",
        shuffle=False,
    )
    assert all(g == 0 for g in result.grades.values())


@pytest.mark.unit
def test_llm_judge_calibrate_passes_when_all_anchors_correct() -> None:
    """LLMJudge.calibrate() returns True when all anchors return their expected grades."""
    responses = [_grade_response({"A": anchor["expected"]}) for anchor in CALIBRATION_ANCHORS]
    backend = FakeChatBackend(responses=responses)

    judge = LLMJudge(chat_backend=backend)
    assert judge.calibrate(api_key="key", endpoint="https://endpoint") is True  # pragma: allowlist secret


@pytest.mark.unit
def test_llm_judge_calibrate_raises_when_too_many_anchors_wrong() -> None:
    """LLMJudge.calibrate() raises JudgeCalibrationError with too many bad grades."""
    responses = [_grade_response({"A": 0}) for _ in CALIBRATION_ANCHORS]
    backend = FakeChatBackend(responses=responses)

    judge = LLMJudge(chat_backend=backend)
    with pytest.raises(JudgeCalibrationError):
        judge.calibrate(api_key="key", endpoint="https://endpoint")  # pragma: allowlist secret


@pytest.mark.unit
def test_llm_judge_calibrate_logs_when_errors_within_threshold() -> None:
    """When 1..CALIBRATION_MAX_ERRORS anchors are wrong, calibrate returns True and logs."""
    # Build responses where the first two anchors return a wrong grade and the rest are correct.
    # That gives us 2 errors total, within the threshold (3), so calibrate returns True
    # but exercises the warning-log branch.
    responses = []
    for i, anchor in enumerate(CALIBRATION_ANCHORS):
        wrong = anchor["expected"] - 1 if anchor["expected"] > 0 else 1
        responses.append(_grade_response({"A": wrong if i < 2 else anchor["expected"]}))
    backend = FakeChatBackend(responses=responses)

    judge = LLMJudge(chat_backend=backend)
    assert judge.calibrate(api_key="key", endpoint="https://endpoint") is True  # pragma: allowlist secret


# ---------------------------------------------------------------------------
# fetch_llm_credentials — DEPRECATED legacy helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fetch_llm_credentials_returns_empties_when_secrets_unavailable() -> None:
    """When LLM secrets are not configured, returns empty strings + default deployment.

    Exercises the ``except Exception`` fallback so callers in legacy free-function
    paths get all-zero grades from the judge rather than a raised error.
    """
    api_key, endpoint, deployment = fetch_llm_credentials()
    # In the test environment kairix.secrets cannot resolve LLM creds — the
    # OSError raised by get_secret(required=True) is caught by fetch_llm_credentials.
    assert api_key == ""
    assert endpoint == ""
    assert deployment == JUDGE_DEPLOYMENT
