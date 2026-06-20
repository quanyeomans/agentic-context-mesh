"""Step definitions for connector_skills.feature.

Drives the real :class:`kairix.connectors.skills.SkillsConnector` against
a ``tmp_path``-rooted fake ``.claude`` tree. No real ``~/.claude`` read —
the fake tree (generic skill names per F32) lets the behaviour assertions
pin the typed ChangeEvent shape, the kind-prefixed item_id, the ISO
modified_at, the Markdown rendering, and the graceful-degrade no-op.

Per F46, this step file reaches the connector through the real
constructor + the ``claude_root`` DI seam (depth <= 2). Direct
construction is permitted in BDD step files when the target is a
Protocol-compliant leaf such as ``SkillsConnector``.

F1-clean: no @patch / kairix module-attribute substitution.
F2-clean: no KAIRIX_* env-var manipulation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from kairix.connectors.skills import SkillsConnector
from kairix.core.protocols import ChangeEvent, RawArtefact

pytestmark = pytest.mark.bdd


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@dataclass
class _Ctx:
    """Per-scenario context — no module-level mutable state."""

    connector: SkillsConnector | None = None
    events: list[ChangeEvent] = field(default_factory=list)
    artefact: RawArtefact | None = None
    skill_name: str = ""


@pytest.fixture
def skills_ctx() -> _Ctx:
    return _Ctx()


# ---------------------------------------------------------------------------
# Givens
# ---------------------------------------------------------------------------


@given(parsers.parse('a host with one installed skill named "{name}"'))
def _given_one_skill(skills_ctx: _Ctx, name: str, tmp_path: Path) -> None:
    root = tmp_path / ".claude"
    _write(
        root / f"plugins/cache/mkt/sp/1.0.0/skills/{name}/SKILL.md",
        f"---\nname: {name}\ndescription: Explore the problem space first.\n---\nUse before any creative work.\n",
    )
    skills_ctx.skill_name = name
    skills_ctx.connector = SkillsConnector(claude_root=root)


@given("a host with no installed skills tree")
def _given_no_tree(skills_ctx: _Ctx, tmp_path: Path) -> None:
    skills_ctx.connector = SkillsConnector(claude_root=tmp_path / "missing" / ".claude")


# ---------------------------------------------------------------------------
# Whens
# ---------------------------------------------------------------------------


@when("the operator runs the skills connector list_changes with no cursor")
def _when_list_changes(skills_ctx: _Ctx) -> None:
    assert skills_ctx.connector is not None, "Given step must run before When"
    skills_ctx.events = list(skills_ctx.connector.list_changes(cursor=None))


@when("the operator fetches the changed skill artefact")
def _when_fetch(skills_ctx: _Ctx) -> None:
    assert skills_ctx.connector is not None
    assert skills_ctx.events, "list_changes must run (and emit) before fetch"
    skills_ctx.artefact = skills_ctx.connector.fetch(skills_ctx.events[0].item_id)


# ---------------------------------------------------------------------------
# Thens
# ---------------------------------------------------------------------------


@then("one skills change event is emitted")
def _one_event(skills_ctx: _Ctx) -> None:
    assert len(skills_ctx.events) == 1, f"expected 1 event, got {len(skills_ctx.events)}: {skills_ctx.events!r}"


@then("the skills change event item id is prefixed with the skill kind")
def _event_prefixed(skills_ctx: _Ctx) -> None:
    item_id = skills_ctx.events[0].item_id
    assert item_id == f"skill:{skills_ctx.skill_name}", f"unexpected item_id: {item_id!r}"


@then("the skills change event carries an ISO-8601 modified_at timestamp")
def _event_has_iso(skills_ctx: _Ctx) -> None:
    modified_at = skills_ctx.events[0].modified_at
    assert modified_at.endswith("Z"), f"modified_at not ISO-8601: {modified_at!r}"


@then("the skills change event's sensitivity tier is internal")
def _event_internal_tier(skills_ctx: _Ctx) -> None:
    tier = skills_ctx.events[0].metadata.get("sensitivity")
    assert tier == "internal", f"event sensitivity is not internal: {tier!r}"


@then("the fetched skills artefact is Markdown")
def _artefact_is_markdown(skills_ctx: _Ctx) -> None:
    assert skills_ctx.artefact is not None, "fetch step must run first"
    assert skills_ctx.artefact.mime == "text/markdown", f"unexpected mime: {skills_ctx.artefact.mime!r}"


@then("the fetched skills artefact contains the skill name")
def _artefact_contains_name(skills_ctx: _Ctx) -> None:
    assert skills_ctx.artefact is not None
    body = skills_ctx.artefact.raw.decode("utf-8")
    assert skills_ctx.skill_name in body, f"rendered Markdown missing skill name: {body!r}"


@then("no skills change events are emitted")
def _no_events(skills_ctx: _Ctx) -> None:
    assert skills_ctx.events == [], f"expected zero events on a host with no tree; got {skills_ctx.events!r}"
