"""F68 failure-injection contract test for :class:`TokenStore`.

* ``store`` → ``unauthorized`` (KV / file backend rejects write)
"""

from __future__ import annotations

import pytest

from kairix.connect.protocols import (
    CapturedTokens,
    ClientCredentials,
    TokenStoreUnauthorizedError,
)
from tests.fakes import FakeTokenStore

pytestmark = pytest.mark.contract


def test_store_unauthorized_when_backend_rejects_write() -> None:
    """A ``store`` call against a backend that refuses → :class:`TokenStoreUnauthorizedError`."""
    store = FakeTokenStore(
        raises=TokenStoreUnauthorizedError("simulated KV permission denied"),
    )
    with pytest.raises(TokenStoreUnauthorizedError, match="simulated KV"):
        store.store(
            scope="connector",
            area="gmail",
            instance=None,
            tokens=CapturedTokens(refresh_token="r", access_token="a", token_uri="https://x/"),
            client=ClientCredentials(client_id="c", client_secret="s"),
        )
