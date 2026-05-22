"""Step implementations for connector_obsidian.feature.

The scenarios drive the real :class:`kairix.connectors.obsidian.ObsidianConnector`
against a temporary vault. Watchdog isn't started for the reconciliation
scenarios — we drive the public ``list_changes`` surface, the connector
internally calls the reconciler when the cursor is ``None``. Per F46,
this binding stays within depth-2 of either the connector's factory
(``make_connector``) or the canonical fake — direct ``ObsidianConnector(...)``
construction is allowed because the connector is itself a Protocol-
compliant leaf (no pipeline composed here).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.obsidian import ObsidianConnector, make_connector
from kairix.core.protocols import ChangeEvent


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    vault_root: Path
    connector: ObsidianConnector | None = None
    last_events: list[ChangeEvent] | None = None
    known_state: dict[str, str] | None = None


@pytest.fixture
def obsidian_ctx(tmp_path: Path) -> _Ctx:
    vault = tmp_path / "vault"
    vault.mkdir()
    return _Ctx(vault_root=vault)


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given("an Obsidian vault containing three markdown notes")
def _vault_with_three_notes(obsidian_ctx: _Ctx) -> None:
    """Seed three notes under the vault root. Names are alphabetically
    sorted so the connector's deterministic ordering is observable.
    """
    for stem, body in (
        ("alpha", "# Alpha\n\nFirst note."),
        ("bravo", "# Bravo\n\nSecond note."),
        ("charlie", "# Charlie\n\nThird note."),
    ):
        (obsidian_ctx.vault_root / f"{stem}.md").write_text(body, encoding="utf-8")
    # Default factory entry — make_connector exercises the entry-point shape.
    obsidian_ctx.connector = make_connector({"vault_root": str(obsidian_ctx.vault_root)})


@given("the connector has already ingested those three notes")
def _connector_already_ingested(obsidian_ctx: _Ctx) -> None:
    """Simulate the orchestration layer remembering the last seen hashes
    for each note. The reconciler uses this to compute drift.
    """
    from kairix.knowledge.reflib.dedup import hash_content

    obsidian_ctx.known_state = {
        path.relative_to(obsidian_ctx.vault_root).as_posix(): hash_content(path.read_text(encoding="utf-8"))
        for path in sorted(obsidian_ctx.vault_root.rglob("*.md"))
    }


@given("the operator deletes one of the notes from the vault", target_fixture="deleted_item_id")
def _delete_one_note(obsidian_ctx: _Ctx) -> str:
    victim = obsidian_ctx.vault_root / "bravo.md"
    victim.unlink()
    return "bravo.md"


@given("the worker is paused so no watchdog event fires for the next edit")
def _worker_paused(obsidian_ctx: _Ctx) -> None:
    """No-op — the scenario doesn't start the watchdog observer, so any
    edit it makes is by definition missed by watchdog and only the
    reconciler can recover it.
    """
    obsidian_ctx.known_state = obsidian_ctx.known_state or {}


@given(
    "the operator edits one of the notes while the worker is paused",
    target_fixture="edited_item_id",
)
def _edit_one_note_while_paused(obsidian_ctx: _Ctx) -> str:
    """Pre-populate the known-state from the *original* content, then edit."""
    from kairix.knowledge.reflib.dedup import hash_content

    obsidian_ctx.known_state = {
        path.relative_to(obsidian_ctx.vault_root).as_posix(): hash_content(path.read_text(encoding="utf-8"))
        for path in sorted(obsidian_ctx.vault_root.rglob("*.md"))
    }
    edited = obsidian_ctx.vault_root / "alpha.md"
    edited.write_text("# Alpha\n\nEdited body — the worker was paused.", encoding="utf-8")
    return "alpha.md"


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator runs the obsidian connector list_changes with no cursor")
def _run_list_changes_no_cursor(obsidian_ctx: _Ctx) -> None:
    assert obsidian_ctx.connector is not None
    # Inject the known-state resolver so the reconciler treats the
    # vault as empty (== first-ever sync, every file is "created").
    obsidian_ctx.connector = ObsidianConnector(
        vault_root=obsidian_ctx.vault_root,
        known_state_resolver=lambda _c: {},
    )
    obsidian_ctx.last_events = list(obsidian_ctx.connector.list_changes(cursor=None))


@when("the operator runs the obsidian connector list_changes with a stale cursor")
def _run_list_changes_with_stale_cursor(obsidian_ctx: _Ctx) -> None:
    assert obsidian_ctx.known_state is not None
    known = obsidian_ctx.known_state  # bind once so the lambda below stays explicit
    obsidian_ctx.connector = ObsidianConnector(
        vault_root=obsidian_ctx.vault_root,
        known_state_resolver=lambda _c: known,
    )
    obsidian_ctx.last_events = list(obsidian_ctx.connector.list_changes(cursor=None))


@when("the operator restarts the worker and runs list_changes")
def _restart_worker_and_run(obsidian_ctx: _Ctx) -> None:
    assert obsidian_ctx.known_state is not None
    known = obsidian_ctx.known_state
    obsidian_ctx.connector = ObsidianConnector(
        vault_root=obsidian_ctx.vault_root,
        known_state_resolver=lambda _c: known,
    )
    obsidian_ctx.last_events = list(obsidian_ctx.connector.list_changes(cursor=None))


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then(parsers.parse("three created change events are emitted in vault-relative order"))
def _three_created(obsidian_ctx: _Ctx) -> None:
    assert obsidian_ctx.last_events is not None
    created = [e for e in obsidian_ctx.last_events if e.op == "created"]
    assert len(created) == 3, f"expected 3 created events, got {created!r}"
    ids = [e.item_id for e in created]
    assert ids == sorted(ids), f"created events not in lexicographic order: {ids!r}"


@then("every change event carries an ISO-8601 modified_at timestamp")
def _events_have_iso_timestamp(obsidian_ctx: _Ctx) -> None:
    from datetime import datetime

    assert obsidian_ctx.last_events
    for ev in obsidian_ctx.last_events:
        # Strict ISO parser — accepts the trailing "Z" form emitted by the connector.
        datetime.fromisoformat(ev.modified_at.replace("Z", "+00:00"))


@then("every change event's item_id is a vault-relative POSIX path")
def _events_have_relative_ids(obsidian_ctx: _Ctx) -> None:
    assert obsidian_ctx.last_events
    for ev in obsidian_ctx.last_events:
        assert not ev.item_id.startswith("/"), f"item_id {ev.item_id!r} is absolute"
        assert "\\" not in ev.item_id, f"item_id {ev.item_id!r} uses windows separators"


@then("a deleted change event is emitted for the removed note's item_id")
def _deleted_event_for_removed(obsidian_ctx: _Ctx, deleted_item_id: str) -> None:
    assert obsidian_ctx.last_events is not None
    deleted = [e for e in obsidian_ctx.last_events if e.op == "deleted"]
    ids = [e.item_id for e in deleted]
    assert deleted_item_id in ids, f"expected {deleted_item_id!r} in {ids!r}"


@then("no created or modified events are emitted for the surviving notes")
def _no_change_for_survivors(obsidian_ctx: _Ctx, deleted_item_id: str) -> None:
    assert obsidian_ctx.last_events is not None
    survivors = {"alpha.md", "charlie.md"} - {deleted_item_id}
    bad = [e for e in obsidian_ctx.last_events if e.item_id in survivors and e.op != "deleted"]
    assert not bad, f"surviving notes got spurious events: {bad!r}"


@then("a modified change event is emitted for the edited note's item_id")
def _modified_event_for_edited(obsidian_ctx: _Ctx, edited_item_id: str) -> None:
    assert obsidian_ctx.last_events is not None
    modified = [e for e in obsidian_ctx.last_events if e.op == "modified"]
    ids = [e.item_id for e in modified]
    assert edited_item_id in ids, f"expected {edited_item_id!r} in modified events {ids!r}"


@then("the change event's source_link round-trips to an obsidian:// URL")
def _source_link_round_trips(obsidian_ctx: _Ctx, edited_item_id: str) -> None:
    assert obsidian_ctx.connector is not None
    link = obsidian_ctx.connector.source_link(edited_item_id)
    assert link.startswith("obsidian://open?vault="), f"unexpected URL: {link!r}"
    assert edited_item_id in link or "alpha.md" in link
