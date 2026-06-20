"""Unit tests for :class:`kairix.connectors.skills.connector.SkillsConnector`.

Drives the real connector against a ``tmp_path``-rooted fake ``.claude``
tree (F32 — generic skill names, never the real ``~/.claude``). Covers
the SourceConnector surface (``list_changes`` / ``fetch`` /
``source_link`` / ``sensitivity_for`` / ``next_cursor`` /
``metadata_for``), the PollConnector + SlimConnector capability surface
(``list_changes_for_container`` / ``retrieve_all_slim_docs``), the F66
per-tick budget, the absent-root graceful degrade, and ``make_connector``
config validation.

F1/F2-clean: the walk root is injected via ``claude_root=``; no @patch,
no env vars. F8 carries ``@pytest.mark.unit``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connectors.skills.connector import (
    CONNECTOR_NAME,
    DEFAULT_SENSITIVITY,
    SkillsConnector,
    make_connector,
)
from kairix.core.protocols import Container, RawArtefact, SourceMetadata

pytestmark = pytest.mark.unit


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _seed_tree(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    _write(
        root / "plugins/cache/mkt/sp/6.0.3/skills/brainstorming/SKILL.md",
        "---\nname: brainstorming\ndescription: Explore the problem space first.\n---\nUse before any creative work.\n",
    )
    _write(
        root / "plugins/cache/mkt/sp/1.0.0/commands/feature-dev.md",
        "---\nname: feature-dev\ndescription: Guided feature development.\n---\nbody\n",
    )
    return root


def test_list_changes_emits_one_event_per_artefact(tmp_path: Path) -> None:
    """Each deduped artefact surfaces as a created/modified ChangeEvent."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    events = list(connector.list_changes(cursor=None))
    item_ids = {ev.item_id for ev in events}
    assert item_ids == {"skill:brainstorming", "command:feature-dev"}
    for ev in events:
        assert ev.op in ("created", "modified")
        assert ":" in ev.item_id
        assert ev.modified_at  # ISO-8601 timestamp


def test_fetch_returns_markdown_artefact(tmp_path: Path) -> None:
    """fetch renders the artefact to markdown bytes with a text/markdown mime."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    list(connector.list_changes(cursor=None))
    artefact = connector.fetch("skill:brainstorming")
    assert isinstance(artefact, RawArtefact)
    assert artefact.mime == "text/markdown"
    body = artefact.raw.decode("utf-8")
    assert "brainstorming" in body
    assert "Explore the problem space first." in body


def test_metadata_for_surfaces_tags(tmp_path: Path) -> None:
    """metadata_for returns SourceMetadata with the capability + kind tags."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    list(connector.list_changes(cursor=None))
    meta = connector.metadata_for("skill:brainstorming")
    assert isinstance(meta, SourceMetadata)
    assert "capability" in meta.tags
    assert "kind:skill" in meta.tags
    assert meta.modified_at  # file mtime


def test_sensitivity_for_is_internal(tmp_path: Path) -> None:
    """The connector ships the documented internal default tier."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    assert connector.sensitivity_for("skill:brainstorming") == DEFAULT_SENSITIVITY == "internal"


def test_source_link_points_at_the_capability(tmp_path: Path) -> None:
    """source_link returns a capability:// URI for the item."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    list(connector.list_changes(cursor=None))
    assert connector.source_link("skill:brainstorming") == "capability://skill/brainstorming"


def test_next_cursor_advances_after_drain(tmp_path: Path) -> None:
    """next_cursor returns the ISO high-water-mark after a clean drain."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    assert connector.next_cursor() is None
    list(connector.list_changes(cursor=None))
    assert connector.next_cursor()  # non-empty after drain


def test_absent_root_yields_no_events_and_no_raise(tmp_path: Path) -> None:
    """Graceful degrade: a host with no ~/.claude produces zero events, never errors."""
    connector = SkillsConnector(claude_root=tmp_path / "missing" / ".claude")
    assert list(connector.list_changes(cursor=None)) == []
    assert list(connector.retrieve_all_slim_docs(_container())) == []


def test_poll_and_slim_surfaces(tmp_path: Path) -> None:
    """list_changes_for_container + retrieve_all_slim_docs enumerate the artefacts."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    container = _container()
    poll_ids = {ev.item_id for ev in connector.list_changes_for_container(container)}
    assert poll_ids == {"skill:brainstorming", "command:feature-dev"}
    slim_ids = set(connector.retrieve_all_slim_docs(container))
    assert slim_ids == {"skill:brainstorming", "command:feature-dev"}


def test_per_tick_budget_declared(tmp_path: Path) -> None:
    """F66: the connector declares per_tick_max_items + the watermark attr."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    assert connector.per_tick_max_items >= 1
    assert connector.disk_watermark_min_free_bytes is None
    assert connector.name == CONNECTOR_NAME == "skills"


def test_cursor_at_artefact_mtime_filters_the_event(tmp_path: Path) -> None:
    """A cursor EQUAL to an artefact's mtime filters it out (<= boundary).

    Pins the ``modified_at <= cursor`` filter: a strict ``<`` would
    (wrongly) re-emit an artefact whose mtime equals the persisted cursor,
    re-ingesting unchanged content on every tick.
    """
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    first = list(connector.list_changes(cursor=None))
    assert first, "first drain must emit events"
    high_water = max(ev.modified_at for ev in first)
    # Replaying with the high-water-mark as cursor must yield nothing — every
    # artefact's mtime is <= the cursor, so all are filtered.
    replay = list(connector.list_changes(cursor=high_water))
    assert replay == [], f"cursor at the high-water-mark must filter all events; got {replay!r}"


def test_make_connector_accepts_budget_of_one(tmp_path: Path) -> None:
    """per_tick_max_items=1 is a valid positive budget (kills the < / <= boundary).

    Pins the ``raw_budget < 1`` guard: a ``<= 1`` would (wrongly) reject the
    smallest valid budget.
    """
    connector = make_connector({"claude_root": str(tmp_path), "per_tick_max_items": 1})
    assert connector.per_tick_max_items == 1


def _container() -> Container:
    return Container(
        cc_pair_id=1,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )


def test_make_connector_defaults_to_home_claude(tmp_path: Path) -> None:
    """make_connector reads an explicit claude_root from config."""
    connector = make_connector({"claude_root": str(_seed_tree(tmp_path))})
    assert connector.name == "skills"
    assert {ev.item_id for ev in connector.list_changes(cursor=None)} == {
        "skill:brainstorming",
        "command:feature-dev",
    }


def test_make_connector_rejects_invalid_sensitivity(tmp_path: Path) -> None:
    """make_connector validates default_sensitivity against the F39 tier set."""
    with pytest.raises(ValueError, match="not a valid F39 tier"):
        make_connector({"claude_root": str(tmp_path), "default_sensitivity": "top-secret"})


def test_make_connector_rejects_bad_budget(tmp_path: Path) -> None:
    """make_connector validates per_tick_max_items is a positive integer."""
    with pytest.raises(ValueError, match="positive integer"):
        make_connector({"claude_root": str(tmp_path), "per_tick_max_items": 0})
