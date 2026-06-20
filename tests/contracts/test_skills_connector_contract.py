"""Per-method failure-injection contract for the skills connector (F68-style).

The F43 behavioural-parity body lives in
:mod:`tests.contracts.test_skills_protocol` (one parametrized assertion
over real + fake). This file is the companion failure-behaviour
coverage: each public ``SourceConnector`` method's failure / edge surface
is driven explicitly (an absent ``~/.claude`` tree, an unknown item_id, a
malformed item_id) and the observable outcome asserted — not just shape
compliance.

``SkillsConnector`` is a concrete class, not a Protocol, so F68's
repo-wide Protocol scan imposes no gate obligation here; this coverage
ships per the connector's failure-mode / graceful-degrade contract
(design §7) so a regression that crashes on a missing tree, or silently
fabricates an artefact for an unknown id, fails loudly.

All trees are ``tmp_path``-rooted (F32 — no real ``~/.claude`` read).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kairix.connectors.skills import SkillsConnector
from kairix.core.protocols import Container, SourceMetadata

pytestmark = pytest.mark.contract


def _container() -> Container:
    return Container(
        cc_pair_id=1,
        container_id="",
        access_state="ACCESSIBLE",
        cursor_token=None,
        last_synced_at=None,
    )


def _absent(tmp_path: Path) -> SkillsConnector:
    """Connector pointed at a non-existent ~/.claude tree (graceful-degrade host).

    The probe path is a never-created subdir of ``tmp_path`` (read-only; the
    connector only stats it), keeping every tree tmp-rooted per the module rule.
    """
    return SkillsConnector(claude_root=tmp_path / "missing" / ".claude")


def test_list_changes_degrades_to_empty_when_tree_absent(tmp_path: Path) -> None:
    """A missing ~/.claude yields zero events and never raises (design §7)."""
    connector = _absent(tmp_path)
    assert list(connector.list_changes(cursor=None)) == []


def test_retrieve_all_slim_docs_degrades_to_empty_when_tree_absent(tmp_path: Path) -> None:
    """The slim enumeration yields nothing when the tree is absent — no raise."""
    connector = _absent(tmp_path)
    assert list(connector.retrieve_all_slim_docs(_container())) == []


def test_list_changes_for_container_degrades_to_empty_when_tree_absent(tmp_path: Path) -> None:
    """The poll surface yields nothing when the tree is absent — no raise."""
    connector = _absent(tmp_path)
    assert list(connector.list_changes_for_container(_container())) == []


def test_fetch_raises_keyerror_for_unknown_item(tmp_path: Path) -> None:
    """fetch on an unknown id raises a fix-pointer KeyError, not a silent artefact."""
    connector = _absent(tmp_path)
    with pytest.raises(KeyError, match="no artefact in cache"):
        connector.fetch("skill:never-installed")


def test_metadata_for_returns_empty_when_item_unknown(tmp_path: Path) -> None:
    """metadata_for returns an empty SourceMetadata for an unknown id."""
    connector = _absent(tmp_path)
    assert connector.metadata_for("skill:never-installed") == SourceMetadata()


def test_source_link_rejects_unprefixed_item_id(tmp_path: Path) -> None:
    """source_link on a malformed (un-prefixed) id raises a fix-pointer ValueError."""
    connector = _absent(tmp_path)
    with pytest.raises(ValueError, match="not kind-prefixed"):
        connector.source_link("brainstorming")
