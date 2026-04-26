"""
Tests for kairix.summaries.generate
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kairix.summaries.generate import (
    _first_n_words,
    generate_l0,
    generate_summaries,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(content: str, total_tokens: int = 42) -> MagicMock:
    """Build a mock httpx.Response for _call_chat."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": total_tokens},
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ---------------------------------------------------------------------------
# generate_l0
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_l0_returns_string():
    """generate_l0() makes one API call and returns the abstract string."""
    expected = "This doc covers Azure Key Vault setup and token rotation."

    with patch("kairix.summaries.generate.httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _make_response(expected)

        result = generate_l0(
            path="docs/azure.md",
            content="Some content about Azure Key Vault.",
            api_key="test-key",
            endpoint="https://test.openai.azure.com",
        )

    assert result == expected
    mock_ctx.post.assert_called_once()


@pytest.mark.unit
def test_generate_l0_uses_first_800_words():
    """generate_l0() passes only the first 800 words to the API."""
    # Build a document with 1200 words (word_0 to word_1199)
    words = [f"word_{i}" for i in range(1200)]
    long_content = " ".join(words)

    captured_body: list[dict] = []

    with patch("kairix.summaries.generate.httpx.Client") as mock_client_cls:
        mock_ctx = MagicMock()
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_ctx.post.return_value = _make_response("abstract")

        def capture_post(url, headers, json):
            captured_body.append(json)
            return _make_response("abstract")

        mock_ctx.post.side_effect = capture_post

        generate_l0(
            path="docs/long.md",
            content=long_content,
            api_key="k",
            endpoint="https://ep",
        )

    assert captured_body, "post was not called"
    user_msg = captured_body[0]["messages"][1]["content"]
    # Should contain word_799 but NOT word_800
    assert "word_799" in user_msg
    assert "word_800" not in user_msg


# ---------------------------------------------------------------------------
# generate_summaries — failure handling
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_generate_summaries_handles_api_failure_gracefully(tmp_path: Path):
    """One failing file should not prevent others from succeeding."""
    good_file = tmp_path / "good.md"
    bad_file = tmp_path / "bad.md"
    good_file.write_text("Good content here.")
    bad_file.write_text("Bad content here.")

    call_count = 0

    def mock_l0(path, content, api_key, endpoint, deployment="gpt-4o-mini"):
        nonlocal call_count
        call_count += 1
        if "bad" in path:
            raise RuntimeError("API error for bad file")
        return "Good abstract."

    with patch("kairix.summaries.generate.generate_l0", side_effect=mock_l0):
        with patch("kairix.summaries.generate.time.sleep"):
            results = generate_summaries(
                paths=[str(good_file), str(bad_file)],
                api_key="k",
                endpoint="https://ep",
                include_l1=False,
                sleep_ms=0,
            )

    # Only the good file should succeed
    assert len(results) == 1
    assert "good" in results[0].path
    assert results[0].l0 == "Good abstract."


@pytest.mark.unit
def test_generate_summaries_sleeps_between_batches(tmp_path: Path):
    """generate_summaries() should sleep between batches of batch_size."""
    # Create 3 files with batch_size=2 → sleep expected after index 2
    files = []
    for i in range(3):
        f = tmp_path / f"doc_{i}.md"
        f.write_text(f"Content of doc {i}.")
        files.append(str(f))

    sleep_calls: list[float] = []

    def mock_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def mock_l0(path, content, api_key, endpoint, deployment="gpt-4o-mini"):
        return f"Abstract for {Path(path).name}."

    with patch("kairix.summaries.generate.generate_l0", side_effect=mock_l0):
        with patch("kairix.summaries.generate.time.sleep", side_effect=mock_sleep):
            results = generate_summaries(
                paths=files,
                api_key="k",
                endpoint="https://ep",
                include_l1=False,
                batch_size=2,
                sleep_ms=200,
            )

    assert len(results) == 3
    # At least one sleep call per file (sleep_ms > 0), plus one batch sleep
    assert len(sleep_calls) >= 1
    # All sleep values should be 0.2 seconds (200ms / 1000)
    for s in sleep_calls:
        assert abs(s - 0.2) < 1e-9


# ---------------------------------------------------------------------------
# _first_n_words helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_first_n_words_truncates():
    text = "one two three four five"
    assert _first_n_words(text, 3) == "one two three"


@pytest.mark.unit
def test_first_n_words_short_doc():
    text = "short doc"
    assert _first_n_words(text, 100) == "short doc"
