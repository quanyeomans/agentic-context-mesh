"""E2E composed path for the topology v2 Wave E m365_email_headers pilot — F48 sibling test.

ADR v2 §"Wave E" calls for the m365_email_headers connector to:

  - emit one :class:`~kairix.core.protocols.Container` per configured
    mailbox via :meth:`iter_containers`
  - emit one root FOLDER plus per-mailbox FOLDER
    :class:`~kairix.core.protocols.HierarchyNode`s parent-before-child
    via :meth:`load_hierarchy`
  - scope :meth:`list_changes_for_container` to a single mailbox via
    a per-mailbox Graph delta cursor

This file is the F48 sibling test for the
``topology_v2_m365_email_headers`` feature flag. It exercises every
layer of the Wave E composed path against the real
:class:`~kairix.connectors.m365_email_headers.connector.M365EmailHeadersConnector`
class, the real :func:`~kairix.core.factory.build_connector_pipeline`
factory, the real ``topology_*`` schema rows, the real
:func:`~kairix.core.connectors.cc_pair.create_cc_pair` lifecycle, and
the real ``topology_hierarchy_nodes`` round-trip.

Per F48 + F47: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``;
runs in CI Stage 4.5 under ``pytest -m e2e``; exercises config →
factory → ingest → query → assertion via the composed production
code paths.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from kairix.connectors.m365_email_headers import (
    M365EmailHeadersConnector,
    M365GraphClient,
)
from kairix.connectors.m365_email_headers.connector import M365Credentials
from kairix.core.connectors.cc_pair import create_cc_pair, transition_cc_pair
from kairix.core.db.schema import create_schema
from kairix.core.factory import build_connector_pipeline
from kairix.core.protocols import Container, HierarchyNode
from kairix.transport.auth.oauth2_client_creds import OAuth2ClientCredsAuth
from tests.fakes import FakeFeatureFlagResolver

pytestmark = pytest.mark.e2e

_FLAG_NAME = "topology_v2_m365_email_headers"
_PRIMARY = "agent-alpha@example.com"
_BETA = "agent-beta@example.com"
_GAMMA = "agent-gamma@example.com"


def _envelope(mailbox: str, idx: int) -> dict[str, Any]:
    return {
        "id": f"{mailbox}-msg-{idx}",
        "from": {"emailAddress": {"address": mailbox}},
        "toRecipients": [{"emailAddress": {"address": mailbox}}],
        "ccRecipients": [],
        "subject": f"Hello {idx} from {mailbox}",
        "sentDateTime": f"2026-05-23T10:{idx:02d}:00Z",
        "receivedDateTime": f"2026-05-23T10:{idx:02d}:01Z",
    }


def _per_mailbox_payload(mailbox: str) -> dict[str, Any]:
    return {
        "value": [_envelope(mailbox, 1)],
        "@odata.deltaLink": (
            f"https://graph.microsoft.com/v1.0/users/{mailbox}"
            f"/mailFolders/AAMkAGFmYWtl-inbox/messages/delta?$deltatoken={mailbox}-tok"
        ),
    }


def _single_inbox_folder_payload() -> dict[str, Any]:
    """One mailFolders response carrying a single ``inbox`` well-known folder (#380)."""
    return {
        "value": [
            {
                "id": "AAMkAGFmYWtl-inbox",
                "displayName": "Inbox",
                "wellKnownName": "inbox",
            },
        ],
    }


def _make_graph_stub() -> tuple[httpx.MockTransport, list[str]]:
    """Construct a MockTransport that records every requested URL.

    The handler matches the requested URL to the correct mailbox UPN
    so per-mailbox delta requests get per-mailbox payloads — this is
    what proves per-mailbox isolation on the composed path.

    #380: the handler also serves the mailFolders enumeration response
    each mailbox now requires before driving per-folder delta. The
    mailFolders response is mailbox-independent (one synthetic inbox
    per mailbox) so the per-mailbox routing still works downstream.
    """
    recorded: list[str] = []

    def _stub(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/oauth2/v2.0/token" in url:
            return httpx.Response(
                200,
                json={
                    "access_token": "fake-bearer",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        if "/mailFolders" in url and "/messages/delta" not in url:
            return httpx.Response(200, json=_single_inbox_folder_payload())
        recorded.append(url)
        for mailbox in (_PRIMARY, _BETA, _GAMMA):
            if f"/users/{mailbox}/" in url:
                return httpx.Response(200, json=_per_mailbox_payload(mailbox))
        return httpx.Response(200, json={"value": []})

    return httpx.MockTransport(_stub), recorded


def _composed_connector_on() -> tuple[M365EmailHeadersConnector, list[str]]:
    """Construct the production connector with the Wave E flag pinned ON.

    Wires three mailboxes to a single shared :class:`httpx.MockTransport`
    so every per-mailbox Graph client routes through the same in-process
    stub. Returns the connector + the recorded-URLs list so tests can
    assert per-mailbox URLs were hit.
    """
    transport, recorded = _make_graph_stub()
    shared = httpx.Client(transport=transport)
    auth = OAuth2ClientCredsAuth(
        tenant_id="fake-tenant",
        client_id="fake-client",
        client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        scope="https://graph.microsoft.com/.default",
        http_client=shared,
    )

    def _builder(resolved_auth: OAuth2ClientCredsAuth, upn: str) -> M365GraphClient:
        return M365GraphClient(user_principal_name=upn, auth=resolved_auth, http_client=shared)

    resolver = FakeFeatureFlagResolver().with_flag(_FLAG_NAME, True)
    connector = M365EmailHeadersConnector(
        user_principal_name=_PRIMARY,
        credentials=M365Credentials(
            tenant_id="fake-tenant",
            client_id="fake-client",
            client_secret="fake-secret-value",  # pragma: allowlist secret — test fixture
        ),
        auth=auth,
        client_builder=_builder,
        mailboxes=[_BETA, _GAMMA],
        flag_reader=resolver.get,
    )
    return connector, recorded


def _bootstrap_e2e_db(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    """Create the production schema + seed the m365-email-headers cc_pair triad."""
    db_path = tmp_path / "kairix.sqlite"
    db = sqlite3.connect(str(db_path))
    create_schema(db, dims=4)
    now = "2026-05-23T00:00:00Z"
    cur = db.execute(
        "INSERT INTO topology_connectors "
        "(kind, name, connector_specific_config, default_sensitivity, created_at, updated_at) "
        "VALUES ('m365_email_headers', 'm365-mailbox-fleet', '{}', 'personal', ?, ?)",
        (now, now),
    )
    connector_id = cur.lastrowid
    assert connector_id is not None
    cc_pair = create_cc_pair(
        db,
        connector_id=int(connector_id),
        credential_id=None,
        name="m365-mailbox-fleet",
    )
    db.commit()
    return db, cc_pair.id


def _persist_hierarchy_nodes(
    db: sqlite3.Connection, *, cc_pair_id: int, nodes: Iterator[HierarchyNode]
) -> list[HierarchyNode]:
    """INSERT every emitted node into the topology_hierarchy_nodes table IN ORDER."""
    persisted: list[HierarchyNode] = []
    for node in nodes:
        persisted.append(node)
        db.execute(
            "INSERT INTO topology_hierarchy_nodes "
            "(cc_pair_id, raw_node_id, raw_parent_id, display_name, "
            "link, node_type, external_access_json, sensitivity_hint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                node.cc_pair_id,
                node.raw_node_id,
                node.raw_parent_id,
                node.display_name,
                node.link,
                node.node_type,
                node.external_access_json,
                node.sensitivity_hint,
            ),
        )
    db.commit()
    return persisted


# ---------------------------------------------------------------------------
# Composed-path signals
# ---------------------------------------------------------------------------


def test_composed_topology_v2_m365_email_headers_path_iter_containers_lands_one_per_mailbox(
    tmp_path: Path,
) -> None:
    """Composed: real connector + real flag-reader → one Container per configured mailbox."""
    connector, _recorded = _composed_connector_on()
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    transition_cc_pair(db, cc_pair_id, "INITIAL_INDEXING")
    containers = list(connector.iter_containers(cc_pair_id=cc_pair_id))
    db.commit()
    ids = [c.container_id for c in containers]
    assert ids == sorted([_PRIMARY, _BETA, _GAMMA]), (
        f"Wave E pilot: expected one Container per configured mailbox in sorted UPN order, got {ids!r}"
    )
    for c in containers:
        assert c.cc_pair_id == cc_pair_id
        assert c.access_state == "ACCESSIBLE"
        assert c.cursor_token is None


def test_composed_topology_v2_m365_email_headers_path_hierarchy_round_trip_preserves_order(
    tmp_path: Path,
) -> None:
    """Composed: real load_hierarchy → persist → read back preserves parent-before-child."""
    connector, _recorded = _composed_connector_on()
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    nodes = _persist_hierarchy_nodes(db, cc_pair_id=cc_pair_id, nodes=connector.load_hierarchy(cc_pair_id=cc_pair_id))
    assert nodes, "Wave E pilot: load_hierarchy must emit at least the root + per-mailbox folders"
    rows = db.execute(
        "SELECT raw_node_id, raw_parent_id FROM topology_hierarchy_nodes WHERE cc_pair_id = ? ORDER BY rowid",
        (cc_pair_id,),
    ).fetchall()
    seen: set[str] = set()
    for raw_id, raw_parent_id in rows:
        if raw_parent_id is not None:
            assert raw_parent_id in seen, (
                f"hierarchy round-trip violates parent-before-child: {raw_id!r} ↛ {raw_parent_id!r}"
            )
        seen.add(raw_id)
    # Confirm the structural shape — root + every configured mailbox.
    raw_ids = {row[0] for row in rows}
    assert "m365-email-headers" in raw_ids
    for mailbox in (_PRIMARY, _BETA, _GAMMA):
        assert mailbox in raw_ids, f"composed path: mailbox {mailbox!r} missing from persisted hierarchy"


def test_composed_topology_v2_m365_email_headers_path_list_changes_scopes_to_mailbox(
    tmp_path: Path,
) -> None:
    """Composed: real connector + real Container → list_changes only emits subtree events.

    Drives :meth:`list_changes_for_container` for one mailbox via the
    real connector and the real per-mailbox Graph client. Only that
    mailbox's URL appears in the recorded request list AND only that
    mailbox's events surface.
    """
    connector, recorded = _composed_connector_on()
    db, cc_pair_id = _bootstrap_e2e_db(tmp_path)
    container = Container(
        cc_pair_id=cc_pair_id,
        container_id=_BETA,
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )
    events = list(connector.list_changes_for_container(container))
    db.commit()
    assert events, "Wave E pilot: per-mailbox Graph client must emit events for the named mailbox"
    for ev in events:
        assert ev.item_id.startswith(f"{_BETA}-"), (
            f"composed path: mailbox scoping must filter cross-mailbox events; got {ev.item_id!r}"
        )
    # Only the beta mailbox's Graph URL appears in the recorded list.
    beta_urls = [u for u in recorded if f"/users/{_BETA}/" in u]
    other_urls = [u for u in recorded if f"/users/{_PRIMARY}/" in u or f"/users/{_GAMMA}/" in u]
    assert beta_urls, f"composed path: expected at least one /users/{_BETA}/ URL in {recorded!r}"
    assert not other_urls, f"composed path: cross-mailbox URL leaked into the per-container drain; got {other_urls!r}"
    # Per-container cursor persistence — the beta mailbox got its own deltaLink.
    cursor = connector.next_cursor_for_container(_BETA)
    assert cursor is not None and _BETA in cursor, (
        f"composed path: per-mailbox deltaLink must be recorded for {_BETA}; got {cursor!r}"
    )


def test_composed_topology_v2_m365_email_headers_path_factory_pipeline_builds(tmp_path: Path) -> None:
    """Composed: factory builds the production pipeline alongside the Wave E connector.

    F46 / F47 contract: BDD + integration tests reach the production
    composition surface via :func:`build_connector_pipeline`. This
    confirms the Wave E pilot is compatible with the existing factory
    (no breaking change to the surrounding pipeline shape).
    """
    db, _cc_pair_id = _bootstrap_e2e_db(tmp_path)
    bronze_root = tmp_path / "bronze"
    bronze_root.mkdir()
    pipeline = build_connector_pipeline(db=db, bronze_root=bronze_root, collection="m365-mailbox-fleet")
    assert pipeline is not None


def test_composed_topology_v2_m365_email_headers_path_flag_registered() -> None:
    """Composed: the flag is in the production registry at default-False / introduce."""
    from kairix.core.features.registry import REGISTRY

    assert _FLAG_NAME in REGISTRY
    entry = REGISTRY[_FLAG_NAME]
    assert entry.default is False
    assert entry.stage == "introduce"
    assert entry.related_spec is not None
    assert "connector-scope-topology" in entry.related_spec
