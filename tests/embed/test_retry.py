"""Unit tests for embed_batch using OpenAI SDK."""

from unittest.mock import MagicMock, patch

import pytest

from kairix.core.embed.embed import embed_batch

API_KEY = "test-key"
ENDPOINT = "https://test.openai.azure.com"
DEPLOYMENT = "text-embedding-3-large"
DIMS = 1536

pytestmark = pytest.mark.unit


def _mock_embedding(index: int, value: float = 0.1) -> MagicMock:
    """Create a mock embedding data item."""
    m = MagicMock()
    m.index = index
    m.embedding = [value] * DIMS
    return m


def _mock_response(texts: list[str], value: float = 0.1) -> MagicMock:
    """Create a mock embeddings.create() response."""
    resp = MagicMock()
    resp.data = [_mock_embedding(i, value) for i in range(len(texts))]
    return resp


class TestEmbedBatch:
    def test_success(self) -> None:
        texts = ["hello", "world"]
        with patch("openai.AzureOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.return_value = _mock_response(texts)
            result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 2
        assert all(len(v) == DIMS for v in result)

    def test_empty_batch_returns_empty(self) -> None:
        result = embed_batch([], API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert result == []

    def test_results_ordered_by_index(self) -> None:
        texts = ["a", "b", "c"]
        resp = MagicMock()
        # Return out-of-order
        resp.data = [
            _mock_embedding(2, 0.3),
            _mock_embedding(0, 0.1),
            _mock_embedding(1, 0.2),
        ]
        with patch("openai.AzureOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.return_value = resp
            result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert result[0][0] == pytest.approx(0.1)
        assert result[1][0] == pytest.approx(0.2)
        assert result[2][0] == pytest.approx(0.3)

    def test_bad_request_splits_batch(self) -> None:
        """BadRequestError on a multi-item batch splits and retries."""
        import openai

        texts = ["a", "b"]
        call_count = 0

        def side_effect(**kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            inp = kwargs.get("input", [])
            if len(inp) > 1:
                raise openai.BadRequestError(
                    message="batch too large",
                    response=MagicMock(status_code=400),
                    body=None,
                )
            return _mock_response(inp)

        with patch("openai.AzureOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = side_effect
            result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 2
        assert call_count == 3  # 1 failed + 2 single-item retries

    def test_bad_request_single_item_raises(self) -> None:
        """BadRequestError on a single-item batch raises."""
        import openai

        texts = ["a"]
        with patch("openai.AzureOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.side_effect = openai.BadRequestError(
                message="bad input",
                response=MagicMock(status_code=400),
                body=None,
            )
            with pytest.raises(openai.BadRequestError):
                embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)

    def test_sdk_creates_client_with_correct_params(self) -> None:
        """Verify the SDK client is configured with the right retry and timeout."""
        texts = ["hello"]
        with patch("openai.AzureOpenAI") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.embeddings.create.return_value = _mock_response(texts)
            embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)

        mock_cls.assert_called_once_with(
            api_key=API_KEY,
            azure_endpoint=ENDPOINT,
            api_version="2024-02-01",
            max_retries=6,
            timeout=60.0,
        )
