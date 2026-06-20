"""Unit tests for the skills connector filesystem walk + dedup (``fs.py``).

Drives :func:`kairix.connectors.skills.fs.iter_skill_artefacts` against a
``tmp_path``-rooted fake ``.claude`` tree — never the real ``~/.claude``
(F32 + test-discipline: scratch under ``tmp_path`` only). Covers:

  * dir-kind mapping (skills / commands / agents + the flat ``~/.claude``
    skills tree),
  * YAML-frontmatter parsing (``name`` / ``description``),
  * dedup-by-name preferring the higher version string,
  * graceful degrade when ``~/.claude`` is absent (empty iterator).

F1/F2-clean: the walk root is injected via ``claude_root=``; no @patch,
no env vars. F8 carries ``@pytest.mark.unit``.

Sabotage proof (executed by the agent, restored on completion): mutate
the dedup "prefer higher version" rule in ``fs.py`` so it keeps the
LOWER version (``new < existing`` instead of ``new > existing``) — then
``test_walk_dedups_by_name_prefers_higher_version`` fails on the
``description == "new"`` assertion. Restoring the original comparison
returns the test to green. (See Step 2.6 report for the observed run.)
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_walk_dedups_by_name_prefers_higher_version(tmp_path: Path) -> None:
    """Two cached versions of one skill collapse to one entry — higher wins."""
    from kairix.connectors.skills.fs import iter_skill_artefacts

    root = tmp_path / ".claude"
    _write(
        root / "plugins/cache/mkt/sp/4.0.0/skills/brainstorming/SKILL.md",
        "---\nname: brainstorming\ndescription: old\n---\nold body\n",
    )
    _write(
        root / "plugins/cache/mkt/sp/6.0.3/skills/brainstorming/SKILL.md",
        "---\nname: brainstorming\ndescription: new\n---\nnew body\n",
    )
    _write(
        root / "skills/ui-review.md",
        "---\nname: ui-review\ndescription: review UI\n---\nbody\n",
    )

    items = {a.name: a for a in iter_skill_artefacts(claude_root=root)}
    assert set(items) == {"brainstorming", "ui-review"}
    assert items["brainstorming"].description == "new"  # higher version wins
    assert items["brainstorming"].body == "new body"
    # The kept artefact carries the higher version segment, parsed from the
    # plugins/cache/<mkt>/<plugin>/<version>/ path (pins fs version extraction).
    assert items["brainstorming"].version == "6.0.3"
    # The flat ~/.claude/skills file has no plugin-cache version segment.
    assert items["ui-review"].version == ""
    assert items["ui-review"].kind == "skill"


def test_walk_tie_on_version_keeps_first_seen(tmp_path: Path) -> None:
    """Equal-version duplicates do not flip-flop — the first (sorted) wins.

    Pins ``_prefers`` to a strict ``>`` (a ``>=`` would let an equal-version
    later artefact replace the first, making the result path-order-dependent).
    The two copies live under sibling marketplace dirs at the SAME version, so
    the kept one must be the lexically-first cache path (``aaa`` before ``zzz``).
    """
    from kairix.connectors.skills.fs import iter_skill_artefacts

    root = tmp_path / ".claude"
    _write(
        root / "plugins/cache/aaa/sp/2.0.0/skills/dup/SKILL.md",
        "---\nname: dup\ndescription: from-aaa\n---\nbody\n",
    )
    _write(
        root / "plugins/cache/zzz/sp/2.0.0/skills/dup/SKILL.md",
        "---\nname: dup\ndescription: from-zzz\n---\nbody\n",
    )
    items = {a.name: a for a in iter_skill_artefacts(claude_root=root)}
    assert set(items) == {"dup"}
    assert items["dup"].description == "from-aaa", "equal-version tie must keep the first-seen (sorted) artefact"


def test_walk_orders_double_digit_versions_numerically(tmp_path: Path) -> None:
    """10.0.0 outranks 9.0.0 — numeric, not lexical, version comparison.

    Pins ``_version_key`` numeric parsing: a pure-lexical compare would
    (wrongly) keep "9.0.0" because the string "9" sorts after "10".
    """
    from kairix.connectors.skills.fs import iter_skill_artefacts

    root = tmp_path / ".claude"
    _write(
        root / "plugins/cache/mkt/sp/9.0.0/skills/big/SKILL.md",
        "---\nname: big\ndescription: nine\n---\nbody\n",
    )
    _write(
        root / "plugins/cache/mkt/sp/10.0.0/skills/big/SKILL.md",
        "---\nname: big\ndescription: ten\n---\nbody\n",
    )
    items = {a.name: a for a in iter_skill_artefacts(claude_root=root)}
    assert items["big"].description == "ten", "10.0.0 must outrank 9.0.0 (numeric compare)"
    assert items["big"].version == "10.0.0"


def test_walk_maps_dir_to_kind(tmp_path: Path) -> None:
    """skills/ → skill, commands/ → command, agents/ → agent."""
    from kairix.connectors.skills.fs import iter_skill_artefacts

    root = tmp_path / ".claude"
    _write(
        root / "plugins/cache/mkt/sp/1.0.0/skills/alpha/SKILL.md",
        "---\nname: alpha\ndescription: a skill\n---\nbody-a\n",
    )
    _write(
        root / "plugins/cache/mkt/sp/1.0.0/commands/beta.md",
        "---\nname: beta\ndescription: a command\n---\nbody-b\n",
    )
    _write(
        root / "plugins/cache/mkt/sp/1.0.0/agents/gamma.md",
        "---\nname: gamma\ndescription: an agent\n---\nbody-c\n",
    )

    kinds = {a.name: a.kind for a in iter_skill_artefacts(claude_root=root)}
    assert kinds == {"alpha": "skill", "beta": "command", "gamma": "agent"}


def test_walk_absent_root_yields_nothing(tmp_path: Path) -> None:
    """Graceful degrade: a missing ~/.claude yields an empty iterator, no raise."""
    from kairix.connectors.skills.fs import iter_skill_artefacts

    missing = tmp_path / "nonexistent" / ".claude"
    assert list(iter_skill_artefacts(claude_root=missing)) == []


def test_walk_skips_malformed_frontmatter(tmp_path: Path) -> None:
    """Per-item isolation: a file with no frontmatter / no name is skipped, the rest land."""
    from kairix.connectors.skills.fs import iter_skill_artefacts

    root = tmp_path / ".claude"
    _write(root / "skills/good.md", "---\nname: good\ndescription: ok\n---\nbody\n")
    _write(root / "skills/no-frontmatter.md", "just a body, no yaml header\n")
    _write(root / "skills/no-name.md", "---\ndescription: missing name\n---\nbody\n")

    names = {a.name for a in iter_skill_artefacts(claude_root=root)}
    assert names == {"good"}
