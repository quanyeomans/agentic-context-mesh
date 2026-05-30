"""F54 integration coverage for the ``topology_v2_gmail`` flag.

Wave E of the connector / collection / scope topology v2 migration is
the per-connector pilot for the gmail connector. When the
``topology_v2_gmail`` flag is ON, the connector
:meth:`list_changes_for_container` drains the History API against the
container's own cursor and records a per-container ``historyId`` via
:meth:`next_cursor_for_container`. When OFF, the connector retains the
Wave B shim shape (``list_changes_for_container`` delegates to the
legacy single-cursor :meth:`list_changes`).

Per F54 (docs/architecture/feature-flag-architecture.md §5): every flag
needs integration coverage exercising both branches via
:class:`tests.fakes.FakeFeatureFlagResolver`. The string literal
``"topology_v2_gmail"`` appears verbatim in every ``with_flag(...)``
call so the F54 check picks it up.

F47 — the connector is constructed via real plugin construction with
the :class:`~kairix.connectors.gmail.GmailConnector` class itself; the
flag is injected through the connector's ``flag_reader`` DI seam and
the Gmail client through the ``client=`` seam. No monkey-patching of
the resolver module.

Sabotage proofs (executed by the agent, mutate → confirm fail → restore):

  1. **Flag-OFF inertness** — flipped the gate in
     :meth:`list_changes_for_container` to ``if not
     self._flag_reader(...)`` (so OFF runs the ON branch); confirmed
     ``test_flag_off_list_changes_for_container_delegates_to_legacy``
     fails because the OFF path no longer threads through
     :meth:`list_changes` and ``next_cursor_for_container`` populates
     unexpectedly; restored.

  2. **Per-mailbox cursor isolation** — replaced
     ``self._next_cursor_by_container[mailbox] =
     self._client.last_history_id()`` in :meth:`_list_changes_scoped`
     with the legacy ``self._next_cursor = ...`` write; confirmed
     ``test_flag_on_per_mailbox_cursor_is_recorded`` fails because the
     per-container map stays empty; restored.

  3. **Cold-start under Wave E** — removed the ``if cursor is None:``
     cold-start branch in :meth:`_list_changes_scoped`; confirmed
     ``test_flag_on_cold_start_seeds_per_container_cursor`` fails
     because the connector tries to drain history against a None
     cursor and raises; restored.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import GmailHeader, GmailMessage, HistoryPage
from kairix.core.protocols import (
    CheckpointedConnector,
    Container,
    PollConnector,
)
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.integration

_FLAG_NAME = "topology_v2_gmail"
_USER = "agent-alpha@example.com"


class _RecordingClient:
    """In-process GmailClient stand-in.

    Records the cursor values passed to ``iter_history_message_ids``
    so the integration test can pin both branches' behaviour without
    a real Gmail roundtrip.
    """

    def __init__(self) -> None:
        self.observed_starts: list[str] = []
        self._last_history_id: str | None = None
        self._tick_counter = 0

    def get_profile_history_id(self) -> str:
        return "cold-start-tip"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = page_token
        self.observed_starts.append(start_history_id)
        self._tick_counter += 1
        new_tip = f"tip-{self._tick_counter}"
        return HistoryPage(
            message_ids=("scripted-msg-1",),
            next_page_token=None,
            history_id=new_tip,
        )

    def iter_history_message_ids(self, *, start_history_id: str) -> Iterator[str]:
        page = self.list_history(start_history_id=start_history_id)
        self._last_history_id = page.history_id
        yield from page.message_ids

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        return GmailMessage(
            message_id=message_id,
            thread_id="t-1",
            history_id="100",
            label_ids=("INBOX",),
            headers=(
                GmailHeader(name="From", value="agent-beta@example.com"),
                GmailHeader(name="To", value=_USER),
                GmailHeader(name="Subject", value="Sample"),
                GmailHeader(name="Date", value="2026-05-28T09:00:00Z"),
            ),
            body=b"body",
            body_mime="text/plain",
            body_truncated=False,
            attachments=(),
        )

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


def _build_connector(*, flag_on: bool) -> GmailConnector:
    """Build the connector under either flag branch.

    F54 — verbatim literal so the both-branch grep picks up the flag
    name. Each branch keeps its own ``with_flag(...)`` call so the
    OFF + ON pattern is mechanically observable.
    """
    if flag_on:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_gmail", True)
    else:
        resolver = FakeFeatureFlagResolver().with_flag("topology_v2_gmail", False)
    return GmailConnector(
        user_email=_USER,
        client=_RecordingClient(),  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
        flag_reader=resolver.get,
    )


# ---------------------------------------------------------------------------
# Registry + Protocol satisfaction (cheap correctness pins)
# ---------------------------------------------------------------------------


def test_topology_v2_gmail_flag_registered() -> None:
    """The flag exists in the registry, defaults False, stage=introduce."""
    from kairix.core.features.registry import REGISTRY

    assert "topology_v2_gmail" in REGISTRY
    entry = REGISTRY["topology_v2_gmail"]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.owner == "connector-framework"
    assert entry.related_spec is not None


def test_connector_gmail_flag_registered() -> None:
    """The connector-slot flag exists in the registry, defaults False."""
    from kairix.core.features.registry import REGISTRY

    assert "connector_gmail" in REGISTRY
    entry = REGISTRY["connector_gmail"]
    assert entry.default is False
    assert entry.stage == "introduce"


def test_gmail_connector_satisfies_poll_and_checkpointed_under_both_branches() -> None:
    """Both branches keep the connector satisfying the capability Protocols."""
    off = _build_connector(flag_on=False)
    on = _build_connector(flag_on=True)
    assert isinstance(off, PollConnector)
    assert isinstance(off, CheckpointedConnector)
    assert isinstance(on, PollConnector)
    assert isinstance(on, CheckpointedConnector)


# ---------------------------------------------------------------------------
# OFF branch — Wave B shim behaviour
# ---------------------------------------------------------------------------


def test_flag_off_load_hierarchy_emits_single_root_node() -> None:
    """OFF: load_hierarchy yields exactly one root FOLDER node."""
    connector = _build_connector(flag_on=False)
    nodes = list(connector.load_hierarchy(cc_pair_id=7))
    assert len(nodes) == 1, f"OFF branch must emit one root FOLDER, got {len(nodes)}"
    assert nodes[0].raw_parent_id is None
    assert nodes[0].node_type == "FOLDER"


def test_flag_off_list_changes_for_container_delegates_to_legacy() -> None:
    """OFF: list_changes_for_container delegates to legacy single-cursor list_changes.

    Sabotage proof for #1: flipping the gate so OFF runs the ON
    branch makes this test fail — ``next_cursor()`` stays None
    (the ON branch writes ``next_cursor_for_container`` instead).
    """
    connector = _build_connector(flag_on=False)
    container = Container(
        cc_pair_id=7,
        container_id=_USER,
        access_state="ACCESSIBLE",
        cursor_token="warm-cursor-from-prior-tick",
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "OFF branch must surface events from the legacy delegate"
    # The OFF path advances ``next_cursor`` (the legacy connector-wide
    # cursor), NOT ``next_cursor_for_container``.
    assert connector.next_cursor() is not None, "OFF branch must populate the legacy connector-wide next_cursor"
    assert connector.next_cursor_for_container(_USER) is None, (
        "OFF branch must NOT populate the per-container cursor map"
    )


# ---------------------------------------------------------------------------
# ON branch — Wave E real implementations
# ---------------------------------------------------------------------------


def test_flag_on_iter_containers_yields_one_per_mailbox() -> None:
    """ON: iter_containers yields one Container for the configured mailbox."""
    connector = _build_connector(flag_on=True)
    containers = list(connector.iter_containers(cc_pair_id=7))
    assert len(containers) == 1
    assert containers[0].container_id == _USER
    assert containers[0].access_state == "ACCESSIBLE"
    assert containers[0].cursor_token is None
    assert containers[0].cc_pair_id == 7


def test_flag_on_per_mailbox_cursor_is_recorded() -> None:
    """ON: next_cursor_for_container populates after a warm drain.

    Sabotage proof for #2: changing the per-container cursor write in
    ``_list_changes_scoped`` to write the legacy ``self._next_cursor``
    instead makes this test fail — the per-container map stays empty.
    """
    connector = _build_connector(flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id=_USER,
        access_state="ACCESSIBLE",
        cursor_token="warm-cursor",
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON branch must surface events from the scoped drain"
    cursor = connector.next_cursor_for_container(_USER)
    assert cursor is not None, "ON branch must populate the per-container cursor map"
    assert connector.next_cursor() is None, "ON branch must NOT populate the legacy connector-wide next_cursor"


def test_flag_on_cold_start_seeds_per_container_cursor() -> None:
    """ON: cold-start (cursor_token=None) seeds the cursor at the live tip.

    Sabotage proof for #3: removing the cold-start branch in
    ``_list_changes_scoped`` makes this test fail — the connector
    tries to drain history against a None cursor and the
    ``next_cursor_for_container`` map stays empty.
    """
    connector = _build_connector(flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id=_USER,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events == [], "cold-start under Wave E must emit zero events (seeding cursor only)"
    cursor = connector.next_cursor_for_container(_USER)
    assert cursor == "cold-start-tip", f"cold-start must seed the per-container cursor at the live tip; got {cursor!r}"


def test_flag_on_per_container_event_carries_mailbox_metadata() -> None:
    """ON: every emitted ChangeEvent carries the mailbox name in metadata.

    Mirrors the M365 email-headers Wave E pattern — the ``mailbox``
    metadata key is the Wave E identification token a downstream
    chunker uses to scope per-mailbox routing.
    """
    connector = _build_connector(flag_on=True)
    container = Container(
        cc_pair_id=7,
        container_id=_USER,
        access_state="ACCESSIBLE",
        cursor_token="warm-cursor",
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    assert events, "ON branch must surface events from the scoped drain"
    for ev in events:
        assert ev.metadata.get("mailbox") == _USER, (
            f"ON: ChangeEvent must carry mailbox in metadata; got {ev.metadata!r}"
        )
