"""Unit tests for Azure API retry logic and error handling."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from kairix.embed.embed import embed_batch

API_KEY = "test-key"
ENDPOINT = "https://test.openai.azure.com"
DEPLOYMENT = "text-embedding-3-large"
DIMS = 1536


def make_mock_response(status_code=200, data=None, headers=None):
    mock = MagicMock()
    mock.status_code = status_code
    mock.headers = headers or {}
    if data:
        mock.json.return_value = data
    if status_code >= 400:
        mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    else:
        mock.raise_for_status.return_value = None
    return mock


def make_embed_response(texts):
    return {"data": [{"index": i, "embedding": [0.1] * DIMS} for i in range(len(texts))]}


class TestEmbedBatch:
    def test_success(self):
        texts = ["hello", "world"]
        with patch("requests.post") as mock_post:
            mock_post.return_value = make_mock_response(200, make_embed_response(texts))
            result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 2
        assert all(len(v) == DIMS for v in result)

    def test_results_ordered_by_index(self):
        texts = ["a", "b", "c"]
        # Return out-of-order
        resp_data = {
            "data": [
                {"index": 2, "embedding": [0.3] * DIMS},
                {"index": 0, "embedding": [0.1] * DIMS},
                {"index": 1, "embedding": [0.2] * DIMS},
            ]
        }
        with patch("requests.post") as mock_post:
            mock_post.return_value = make_mock_response(200, resp_data)
            result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        # Should be reordered by index
        assert result[0][0] == pytest.approx(0.1)
        assert result[1][0] == pytest.approx(0.2)
        assert result[2][0] == pytest.approx(0.3)

    def test_retries_on_429(self):
        texts = ["hello"]
        rate_limit = make_mock_response(429, headers={"Retry-After": "0"})
        success = make_mock_response(200, make_embed_response(texts))

        with patch("requests.post", side_effect=[rate_limit, success]):
            with patch("time.sleep"):
                result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 1

    def test_retries_on_500(self):
        texts = ["hello"]
        server_error = make_mock_response(500)
        success = make_mock_response(200, make_embed_response(texts))

        with patch("requests.post", side_effect=[server_error, success]):
            with patch("time.sleep"):
                result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 1

    def test_raises_after_max_retries(self):
        texts = ["hello"]
        server_error = make_mock_response(500)

        with patch("requests.post", return_value=server_error):
            with patch("time.sleep"):
                with pytest.raises(RuntimeError, match="failed after"):
                    embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)

    def test_400_raises_immediately(self):
        texts = ["hello"]
        bad_request = make_mock_response(400)

        with patch("requests.post", return_value=bad_request):
            with pytest.raises(requests.HTTPError):
                embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)

    def test_timeout_retries(self):
        texts = ["hello"]
        success = make_mock_response(200, make_embed_response(texts))

        with patch("requests.post", side_effect=[requests.Timeout, success]):
            with patch("time.sleep"):
                result = embed_batch(texts, API_KEY, ENDPOINT, DEPLOYMENT, DIMS)
        assert len(result) == 1

    def test_missing_api_key_raises(self):
        import os

        env_backup = os.environ.pop("AZURE_OPENAI_API_KEY", None)
        env_backup2 = os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
        try:
            from kairix.embed.embed import _get_azure_config

            with pytest.raises(EnvironmentError, match="AZURE_OPENAI_API_KEY"):
                _get_azure_config()
        finally:
            if env_backup:
                os.environ["AZURE_OPENAI_API_KEY"] = env_backup
            if env_backup2:
                os.environ["AZURE_OPENAI_ENDPOINT"] = env_backup2
