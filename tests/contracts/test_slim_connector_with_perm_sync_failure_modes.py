"""F68 (ADR-024 Bundle A) — failure-mode contract for :class:`SlimConnectorWithPermSync`.

One method (``retrieve_all_slim_docs_with_perms``). Failure surface:

  * ``raises`` — surfaces typed exception when the per-doc ACL endpoint
    fails (SharePoint Graph 429, Drive 403, etc.); orchestrator must
    NOT silently fall back to "no perms" because that would either
    grant blanket access OR mass-deny.
  * ``returns_empty`` — empty iterator when the container has no items
    requiring perm-sync.
  * ``unauthorized`` — typed exception when credentials are expired
    (perm-sync needs a separate auth scope than slim-listing).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.core.protocols import SlimConnectorWithPermSync
from tests.fakes import FakeSlimConnectorWithPermSync

pytestmark = pytest.mark.contract


class _FailingSlimConnectorWithPermSync:
    """Inline :class:`SlimConnectorWithPermSync` with raises-knobs."""

    def __init__(
        self,
        *,
        raises: BaseException | None = None,
        raises_unauthorized: BaseException | None = None,
    ) -> None:
        self._raises = raises
        self._raises_unauthorized = raises_unauthorized

    def retrieve_all_slim_docs_with_perms(self, container: Any) -> Iterator[tuple[str, str]]:
        del container
        if self._raises_unauthorized is not None:
            raise self._raises_unauthorized
        if self._raises is not None:
            raise self._raises
        return iter([])


def test_retrieve_all_slim_docs_with_perms_raises_propagates_typed_exception() -> None:
    """A perm-sync backend failure surfaces — orchestrator must NOT
    interpret a silent empty list as "no permissions" (that would
    either grant blanket access or mass-deny).

    Sabotage proof: change the inline fake to ``return iter([])`` on
    raise. Re-run: pytest.raises sees nothing. Restored.
    """
    conn: SlimConnectorWithPermSync = _FailingSlimConnectorWithPermSync(
        raises=RuntimeError("F68-perm-raises"),
    )
    with pytest.raises(RuntimeError, match="F68-perm-raises"):
        list(conn.retrieve_all_slim_docs_with_perms(container=object()))


def test_retrieve_all_slim_docs_with_perms_returns_empty_when_container_empty() -> None:
    """Empty iterator when the container has no items requiring
    perm-sync — callers iterate without a null check.

    Sabotage proof: change ``FakeSlimConnectorWithPermSync`` to
    ``return iter([("phantom", "ACL")])`` on empty containers. Re-run:
    the ``== []`` assertion fails. Restored.
    """
    conn: SlimConnectorWithPermSync = FakeSlimConnectorWithPermSync()
    out = list(conn.retrieve_all_slim_docs_with_perms(container=object()))
    assert out == [], f"empty container must yield []; got {out!r}"


def test_retrieve_all_slim_docs_with_perms_unauthorized_raises_typed_exception() -> None:
    """A perm-sync auth failure (expired credentials, revoked scope)
    surfaces as a typed exception so the operator-facing layer can
    re-prompt for consent rather than silently mass-revoking ACLs.

    Sabotage proof: drop the ``raise self._raises_unauthorized`` branch
    in the inline fake. Re-run: pytest.raises sees nothing. Restored.
    """
    conn: SlimConnectorWithPermSync = _FailingSlimConnectorWithPermSync(
        raises_unauthorized=PermissionError("F68-perm-unauthorized"),
    )
    with pytest.raises(PermissionError, match="F68-perm-unauthorized"):
        list(conn.retrieve_all_slim_docs_with_perms(container=object()))
