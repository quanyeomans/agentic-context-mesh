"""Contract test for the Gmail connector plugin (F43).

Exercises the canonical fake (:class:`tests.fakes.FakeGmailConnector`)
AND the real implementation
(:class:`kairix.connectors.gmail.GmailConnector`) through the same
:class:`~kairix.core.protocols.SourceConnector` Protocol assertions.

F43 requires this pairing — without it the fake can drift away from the
real wire (or vice versa) and the production path silently diverges
from what BDD / unit tests measure.

Real-impl path is driven against a scripted ``_ScriptedGmailClient``
satisfying the :class:`GmailClient` shape via the public ``client=``
constructor seam — no real Gmail roundtrip and no real secret
resolution happens.

Sabotage proof (executed by agent, restored on completion):

  * Removing the ``list_changes`` method from
    :class:`GmailConnector` flips the SourceConnector isinstance
    check to False; deleting the corresponding attribute from
    :class:`FakeGmailConnector` flips the fake check to False.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from kairix.connectors.gmail import GmailConnector
from kairix.connectors.gmail.client import (
    GmailHeader,
    GmailMessage,
    HistoryPage,
)
from kairix.core.protocols import (
    ChangeEvent,
    CheckpointedConnector,
    Container,
    PollConnector,
    RawArtefact,
    SourceConnector,
    SourceMetadata,
)
from tests.fakes import FakeGmailConnector


class _ScriptedGmailClient:
    """Internal scripted GmailClient-shape collaborator.

    Returns deterministic responses for History + Message calls so the
    real :class:`GmailConnector` can be exercised against the Protocol
    surface without any real Gmail roundtrip.
    """

    def __init__(self, *, messages: list[dict[str, Any]] | None = None) -> None:
        self._messages: list[GmailMessage] = []
        for entry in messages or []:
            headers = tuple(GmailHeader(name=k, value=v) for k, v in entry.get("headers", {}).items())
            self._messages.append(
                GmailMessage(
                    message_id=str(entry.get("id", "scripted-msg")),
                    thread_id=str(entry.get("thread_id", "scripted-thread")),
                    history_id=str(entry.get("history_id", "1000")),
                    label_ids=tuple(entry.get("label_ids", ())),
                    headers=headers,
                    body=entry.get("body", b"plain text body"),
                    body_mime=entry.get("body_mime", "text/plain"),
                    body_truncated=bool(entry.get("body_truncated", False)),
                    attachments=(),
                )
            )
        self._by_id: dict[str, GmailMessage] = {m.message_id: m for m in self._messages}
        self._last_history_id: str | None = None

    def get_profile_history_id(self) -> str:
        return "cold-start-tip"

    def list_history(self, *, start_history_id: str, page_token: str | None = None) -> HistoryPage:
        _ = (start_history_id, page_token)
        return HistoryPage(
            message_ids=tuple(m.message_id for m in self._messages),
            next_page_token=None,
            history_id="final-history-id",
        )

    def iter_history_message_ids(self, *, start_history_id: str) -> Any:
        _ = start_history_id
        self._last_history_id = "final-history-id"
        for message in self._messages:
            yield message.message_id

    def last_history_id(self) -> str | None:
        return self._last_history_id

    def get_message(self, message_id: str) -> GmailMessage:
        return self._by_id[message_id]

    def stats(self) -> Any:
        from kairix.connectors.gmail.client import GmailStatsSnapshot

        return GmailStatsSnapshot(requests=0, rate_limited_403_total=0, token_refreshes=0)

    def invalidate_token(self) -> None:
        return None


_SEED_HEADERS = {
    "Subject": "Project update",
    "From": "agent-alpha@example.com",
    "To": "agent-beta@example.com",
    "Date": "2026-05-28T09:00:00Z",
}


def _real_factory() -> SourceConnector:
    """Real-impl factory — drives the scripted client through a cold-start
    + warm tick so the connector cache is populated for fetch().
    """
    client = _ScriptedGmailClient(
        messages=[
            {
                "id": "gm-msg-1",
                "thread_id": "gm-thread-1",
                "history_id": "1001",
                "headers": _SEED_HEADERS,
                "body": b"Body of project update.",
                "body_mime": "text/plain",
            }
        ]
    )
    connector = GmailConnector(user_email="agent-alpha@example.com", client=client)  # type: ignore[arg-type]  # F3 rationale: local stub mirrors GmailClient shape but isn't typed as the Protocol — boundary-only suppression for the test seam
    # Cold-start seeds the cursor; second call drains the scripted page.
    list(connector.list_changes(cursor=None))
    list(connector.list_changes(cursor="cold-start-tip"))
    return connector


def _fake_factory() -> SourceConnector:
    """Canonical fake factory — pre-seeded message envelope."""
    return FakeGmailConnector(
        user_email="agent-alpha@example.com",
        messages=[
            {
                "id": "gm-msg-1",
                "thread_id": "gm-thread-1",
                "from": "agent-alpha@example.com",
                "to": "agent-beta@example.com",
                "subject": "Project update",
                "date": "2026-05-28T09:00:00Z",
                "body": b"Body of project update.",
            }
        ],
    )


_FACTORIES: list[tuple[str, Callable[[], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_gmail_connector_satisfies_source_connector_protocol(name: str, factory: Callable[[], SourceConnector]) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol.

    Sabotage-proof: removing ``list_changes`` from
    :class:`GmailConnector` flips the real-impl isinstance check to
    False; deleting the corresponding attribute from
    :class:`FakeGmailConnector` flips the fake check to False.
    """
    connector = factory()
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "gmail"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_gmail_connector_pair_source_link_round_trips(name: str, factory: Callable[[], SourceConnector]) -> None:
    """Both implementations round-trip source_link to a mail.google.com URL."""
    connector = factory()
    link = connector.source_link("gm-msg-1")
    assert link.startswith("https://mail.google.com/mail/"), f"{name!r} produced unexpected link: {link!r}"


@pytest.mark.contract
@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_gmail_connector_pair_sensitivity_for_returns_client_confidential(
    name: str, factory: Callable[[], SourceConnector]
) -> None:
    """Both implementations default to ``client-confidential`` per the Gmail spec."""
    connector = factory()
    tier = connector.sensitivity_for("gm-msg-1")
    assert tier == "client-confidential", f"{name!r} produced unexpected sensitivity: {tier!r}"


@pytest.mark.contract
def test_gmail_connector_satisfies_source_connector_protocol_legacy() -> None:
    """Legacy single-impl shape kept for context — covered by the parametrised pair above."""
    connector = _real_factory()
    assert isinstance(connector, SourceConnector), "GmailConnector must satisfy SourceConnector"
    assert connector.name == "gmail"


@pytest.mark.contract
def test_gmail_connector_satisfies_capability_protocols() -> None:
    """The connector satisfies the Poll + Checkpointed capability Protocols (F56)."""
    connector = _real_factory()
    assert isinstance(connector, PollConnector), "GmailConnector must satisfy PollConnector"
    assert isinstance(connector, CheckpointedConnector), "GmailConnector must satisfy CheckpointedConnector"


@pytest.mark.contract
def test_gmail_connector_list_changes_returns_change_events() -> None:
    """list_changes yields :class:`ChangeEvent` instances on the warm tick."""
    connector = _real_factory()
    # After cold-start + warm tick the connector cache has events from
    # the most recent drain — replay the same drain shape from cold.
    fresh_client = _ScriptedGmailClient(
        messages=[
            {
                "id": "gm-msg-x",
                "thread_id": "gm-thread-x",
                "history_id": "9001",
                "headers": _SEED_HEADERS,
                "body": b"body",
            }
        ]
    )
    fresh_connector = GmailConnector(user_email="agent-alpha@example.com", client=fresh_client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    _ = connector  # keep the smoke fixture live for the suite shape
    list(fresh_connector.list_changes(cursor=None))  # cold-start, no events
    events = list(fresh_connector.list_changes(cursor="cold-start-tip"))
    assert events, "warm tick must produce events from the scripted page"
    for ev in events:
        assert isinstance(ev, ChangeEvent)
        assert ev.op == "created"
        assert ev.item_id == "gm-msg-x"


@pytest.mark.contract
def test_gmail_connector_fetch_returns_raw_artefact() -> None:
    """fetch returns a :class:`RawArtefact` from the cached message body."""
    connector = _real_factory()
    artefact = connector.fetch("gm-msg-1")
    assert isinstance(artefact, RawArtefact)
    assert artefact.raw == b"Body of project update."
    assert artefact.mime == "text/plain"
    assert artefact.fetched_at.endswith("Z") or "+" in artefact.fetched_at


@pytest.mark.contract
def test_gmail_connector_source_link_round_trips_to_gmail_web_inbox() -> None:
    """source_link returns a mail.google.com URL carrying the message id."""
    connector = _real_factory()
    link = connector.source_link("gm-msg-1")
    assert link.startswith("https://mail.google.com/mail/u/0/#inbox/")
    assert "gm-msg-1" in link


@pytest.mark.contract
def test_gmail_connector_sensitivity_for_returns_default_tier() -> None:
    """sensitivity_for returns the default ``client-confidential`` tier per the Gmail spec."""
    connector = _real_factory()
    tier = connector.sensitivity_for("gm-msg-1")
    assert tier == "client-confidential", (
        f"Gmail defaults to client-confidential (email is more sensitive than docs); got {tier!r}"
    )


@pytest.mark.contract
def test_gmail_connector_next_cursor_advances_after_drain() -> None:
    """next_cursor populates with the final historyId after a successful list_changes drain."""
    connector = _real_factory()
    cursor = connector.next_cursor()
    assert cursor == "final-history-id", f"GmailConnector.next_cursor must surface the final historyId; got {cursor!r}"


@pytest.mark.contract
def test_gmail_connector_metadata_for_surfaces_envelope_headers() -> None:
    """metadata_for lifts Subject / From / To / Date from the cached headers (F65)."""
    connector = _real_factory()
    metadata = connector.metadata_for("gm-msg-1")
    assert isinstance(metadata, SourceMetadata)
    assert metadata.author == "agent-alpha@example.com"
    assert metadata.author_email == "agent-alpha@example.com"
    assert metadata.modified_at == "2026-05-28T09:00:00Z"
    assert "agent-beta@example.com" in metadata.tags
    assert metadata.properties.get("subject") == "Project update"
    assert metadata.properties.get("thread_id") == "gm-thread-1"


@pytest.mark.contract
def test_gmail_connector_metadata_for_missing_item_returns_empty() -> None:
    """metadata_for returns an empty :class:`SourceMetadata` on cache miss."""
    connector = _real_factory()
    metadata = connector.metadata_for("never-seen")
    assert metadata.author is None
    assert metadata.tags == ()
    assert dict(metadata.properties) == {}


def _build_real_for_capability_checks() -> GmailConnector:
    """Build a real GmailConnector for the iter_containers + load_hierarchy
    capability assertions; returns the concrete type so mypy can resolve
    the capability-method attributes on it.
    """
    client = _ScriptedGmailClient(messages=[{"id": "gm-msg-cap", "headers": _SEED_HEADERS, "body": b"x"}])
    return GmailConnector(user_email="agent-alpha@example.com", client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.


@pytest.mark.contract
def test_gmail_connector_iter_containers_yields_one_per_mailbox() -> None:
    """iter_containers emits exactly one Container for the configured mailbox."""
    connector = _build_real_for_capability_checks()
    containers = list(connector.iter_containers(cc_pair_id=42))
    assert len(containers) == 1
    assert containers[0].cc_pair_id == 42
    assert containers[0].container_id == "agent-alpha@example.com"
    assert containers[0].access_state == "ACCESSIBLE"


@pytest.mark.contract
def test_gmail_connector_load_hierarchy_emits_single_root_folder() -> None:
    """load_hierarchy emits one root FOLDER for the mailbox (Wave E shim)."""
    connector = _build_real_for_capability_checks()
    nodes = list(connector.load_hierarchy(cc_pair_id=42))
    assert len(nodes) == 1
    root = nodes[0]
    assert root.raw_parent_id is None
    assert root.node_type == "FOLDER"
    assert "Gmail" in root.display_name


@pytest.mark.contract
def test_gmail_connector_load_from_checkpoint_delegates_to_list_changes() -> None:
    """load_from_checkpoint is the CheckpointedConnector shim — forwards
    the checkpoint string into list_changes."""
    client = _ScriptedGmailClient(messages=[{"id": "gm-msg-ck", "headers": _SEED_HEADERS, "body": b"x"}])
    connector = GmailConnector(user_email="agent-alpha@example.com", client=client)  # type: ignore[arg-type]  # F3 rationale: scripted client stand-in.
    container = Container(
        cc_pair_id=1,
        container_id="agent-alpha@example.com",
        access_state="ACCESSIBLE",
        cursor_token="seed",
        last_synced_at=None,
    )
    events = list(connector.load_from_checkpoint(container, "warm-checkpoint"))
    assert events, "load_from_checkpoint must surface events from the underlying list_changes drain"
    assert events[0].item_id == "gm-msg-ck"
