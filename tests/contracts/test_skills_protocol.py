"""Contract test for the skills connector plugin (F43 behavioural parity).

Exercises the canonical fake (:class:`tests.fakes.FakeSourceConnector`)
AND the real implementation
(:class:`kairix.connectors.skills.SkillsConnector`) through the same
:class:`~kairix.core.protocols.SourceConnector` Protocol assertions in
ONE parametrized body per behaviour (F43). Without the pairing the fake
can drift from the real wire (or vice versa) and the production path
silently diverges from what BDD / unit tests measure.

Every ``test_*`` function here is parametrized over the
``(name, factory)`` pair so the SAME assertion runs against both the real
and fake implementation — the F43 LIMB-2 requirement. The per-method
failure-injection coverage lives in
:mod:`tests.contracts.test_skills_connector_contract` (F68-style
behaviour-under-failure).

The real-impl path is driven against a ``tmp_path``-rooted fake
``.claude`` tree (F32 — generic skill names, no real ``~/.claude``).

Sabotage proofs:
  * Removing ``list_changes`` from :class:`SkillsConnector` flips
    ``test_connector_satisfies_source_connector_protocol`` (real branch).
  * Replacing ``fetch``'s return with plain ``bytes`` breaks
    ``test_connector_fetch_returns_markdown_artefact``.
  * Mutating :data:`DEFAULT_SENSITIVITY` to ``"public"`` flips
    ``test_connector_default_sensitivity_is_internal``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from kairix.connectors.skills import DEFAULT_SENSITIVITY, SkillsConnector
from kairix.core.protocols import ChangeEvent, RawArtefact, SourceConnector
from tests.fakes import FakeSourceConnector

pytestmark = pytest.mark.contract

_SKILL_NAME = "brainstorming"
_ITEM_ID = f"skill:{_SKILL_NAME}"


def _seed_tree(tmp_path: Path) -> Path:
    root = tmp_path / ".claude"
    skill = root / f"plugins/cache/mkt/sp/3.0.0/skills/{_SKILL_NAME}/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        f"---\nname: {_SKILL_NAME}\ndescription: Explore the problem space first.\n---\nUse before creative work.\n",
        encoding="utf-8",
    )
    return root


def _fake_factory(tmp_path: Path) -> SourceConnector:
    """Canonical fake factory — seeds one markdown artefact event."""
    return FakeSourceConnector(
        name="skills",
        events=[ChangeEvent(op="created", item_id=f"{_ITEM_ID}.md", modified_at="2026-06-20T00:00:00Z")],
        content={f"{_ITEM_ID}.md": b"# brainstorming\n\nExplore the problem space first.\n"},
        sensitivity="internal",
    )


def _real_factory(tmp_path: Path) -> SourceConnector:
    """Real-impl factory — SkillsConnector over a tmp_path fake tree, cache primed."""
    connector = SkillsConnector(claude_root=_seed_tree(tmp_path))
    list(connector.list_changes(cursor=None))
    return connector


_FACTORIES: list[tuple[str, Callable[[Path], SourceConnector]]] = [
    ("fake", _fake_factory),
    ("real", _real_factory),
]


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_satisfies_source_connector_protocol(
    name: str, factory: Callable[[Path], SourceConnector], tmp_path: Path
) -> None:
    """F43: both fake and real impl satisfy the runtime-checkable Protocol."""
    connector = factory(tmp_path)
    assert isinstance(connector, SourceConnector), f"{name!r} factory output is not a SourceConnector"
    assert connector.name == "skills"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_list_changes_returns_change_events(
    name: str, factory: Callable[[Path], SourceConnector], tmp_path: Path
) -> None:
    """Both implementations stream :class:`ChangeEvent` instances."""
    connector = factory(tmp_path)
    events = list(connector.list_changes(cursor=None))
    assert events, f"{name!r} factory produced no events"
    for ev in events:
        assert isinstance(ev, ChangeEvent), f"{name!r} yielded a non-ChangeEvent: {ev!r}"
        assert ev.op in ("created", "modified", "archived", "deleted", "access_lost")


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_fetch_returns_markdown_artefact(
    name: str, factory: Callable[[Path], SourceConnector], tmp_path: Path
) -> None:
    """Both implementations satisfy the ``fetch`` -> :class:`RawArtefact` shape."""
    connector = factory(tmp_path)
    item_id = next(iter(connector.list_changes(cursor=None))).item_id
    artefact = connector.fetch(item_id)
    assert isinstance(artefact, RawArtefact), f"{name!r} fetch did not return a RawArtefact: {artefact!r}"
    assert artefact.mime == "text/markdown", f"{name!r} fetch mime is wrong: {artefact.mime!r}"
    assert artefact.raw, f"{name!r} fetch raw bytes is empty"
    assert _SKILL_NAME.encode() in artefact.raw, f"{name!r} rendered body missing skill name"


@pytest.mark.parametrize("name,factory", _FACTORIES)
def test_connector_default_sensitivity_is_internal(
    name: str, factory: Callable[[Path], SourceConnector], tmp_path: Path
) -> None:
    """``sensitivity_for`` returns the documented default ``internal`` tier."""
    connector = factory(tmp_path)
    item_id = next(iter(connector.list_changes(cursor=None))).item_id
    tier = connector.sensitivity_for(item_id)
    assert tier == DEFAULT_SENSITIVITY == "internal", f"{name!r} returned unexpected sensitivity: {tier!r}"
