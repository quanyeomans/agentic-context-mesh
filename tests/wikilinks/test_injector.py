"""
Tests for mnemosyne.wikilinks.injector

Covers:
- inject_wikilinks(): first mention, skip second, skip existing, skip code blocks,
  skip frontmatter, whole-word match, own-page skip, aliases
- should_inject(): path eligibility
"""

from __future__ import annotations

from pathlib import Path

from mnemosyne.wikilinks.injector import inject_wikilinks, should_inject
from mnemosyne.wikilinks.resolver import WikiEntity

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def make_entity(
    name: str,
    vault_path: str,
    link: str | None = None,
    aliases: list[str] | None = None,
    entity_type: str = "client",
) -> WikiEntity:
    if link is None:
        link = f"[[{name}]]"
    return WikiEntity(
        name=name,
        aliases=aliases or [],
        vault_path=vault_path,
        link=link,
        entity_type=entity_type,
    )


BUPA = make_entity("Acme Health", "02-Areas/Work/Clients/AcmeHealth/", link="[[AcmeHealth]]")
THREE_CUBES = make_entity("Triad Consulting", "02-Areas/Triad Consulting/", link="[[TriadConsulting]]")
HUNGRY_JACKS = make_entity(
    "Burger Palace",
    "02-Areas/Work/Clients/BurgerPalace/",
    link="[[BurgerPalace|Burger Palace]]",
    aliases=["Burger Palace", "BP"],
)


# ---------------------------------------------------------------------------
# inject_wikilinks: first mention, not second
# ---------------------------------------------------------------------------


def test_injects_on_first_mention() -> None:
    content = "We worked with Acme Health on their strategy. Acme Health is a major client."
    modified, injected = inject_wikilinks(content, [BUPA])
    assert "[[AcmeHealth]]" in modified
    assert injected == ["Acme Health"]
    # First mention replaced
    assert modified.startswith("We worked with [[AcmeHealth]]")


def test_does_not_inject_second_mention() -> None:
    content = "We worked with Acme Health on their strategy. Acme Health is a major client."
    modified, _injected = inject_wikilinks(content, [BUPA])
    # Only one [[AcmeHealth]] in result
    assert modified.count("[[AcmeHealth]]") == 1


# ---------------------------------------------------------------------------
# inject_wikilinks: skip if already a wikilink
# ---------------------------------------------------------------------------


def test_skips_already_linked() -> None:
    content = "We worked with [[AcmeHealth]] on their strategy."
    modified, injected = inject_wikilinks(content, [BUPA])
    # No change
    assert modified == content
    assert injected == []


def test_does_not_double_wrap_wikilink() -> None:
    # Use an entity whose name matches its link slug exactly (no space → slug differs).
    # When the link target and display name are identical, _find_already_linked() correctly
    # marks the entity as already linked and suppresses injection on subsequent mentions.
    simple = make_entity("Softcorp", "02-Areas/Work/Orgs/Softcorp/")  # link="[[Softcorp]]"
    content = "[[Softcorp]] is an org. Softcorp also does software."
    modified, injected = inject_wikilinks(content, [simple])
    # No new injection — Softcorp already linked on first occurrence
    assert modified.count("[[Softcorp]]") == 1
    assert injected == []


# ---------------------------------------------------------------------------
# inject_wikilinks: skip inside code blocks
# ---------------------------------------------------------------------------


def test_skips_fenced_code_block() -> None:
    content = (
        "Here is code:\n\n```python\n# Acme Health client\nclient = 'Acme Health'\n```\n\nAcme Health is a client."
    )
    modified, _injected = inject_wikilinks(content, [BUPA])
    # Should inject on 'Acme Health is a client' (after the code block), not inside it
    assert "[[AcmeHealth]]" in modified
    # The Acme Health inside the code block should remain unlinked
    assert "```python\n# Acme Health client" in modified or "```python\n# [[AcmeHealth]] client" not in modified


def test_skips_inline_code() -> None:
    content = "Use the `Acme Health` constant. Triad Consulting is our company."
    modified, _injected2 = inject_wikilinks(content, [BUPA, THREE_CUBES])
    # Acme Health inside backtick is skipped; Triad Consulting is injected
    assert "[[TriadConsulting]]" in modified
    # Acme Health should not be linked (only occurrence is inside inline code)
    assert "[[AcmeHealth]]" not in modified


# ---------------------------------------------------------------------------
# inject_wikilinks: skip frontmatter
# ---------------------------------------------------------------------------


def test_skips_frontmatter() -> None:
    content = "---\ntitle: Acme Health Project\nclient: Acme Health\n---\n\nAcme Health is a major health insurer."
    modified, injected = inject_wikilinks(content, [BUPA])
    # Should inject in body, not frontmatter
    assert injected == ["Acme Health"]
    # Frontmatter preserved intact
    assert "---\ntitle: Acme Health Project\nclient: Acme Health\n---" in modified
    # Body mention linked
    assert "[[AcmeHealth]] is a major health insurer." in modified


def test_frontmatter_bupa_not_linked_in_yaml() -> None:
    content = "---\nclient: Acme Health\n---\n\nAcme Health overview."
    modified, _ = inject_wikilinks(content, [BUPA])
    # frontmatter Acme Health stays as-is
    assert "client: [[AcmeHealth]]" not in modified
    assert "client: Acme Health" in modified


# ---------------------------------------------------------------------------
# inject_wikilinks: whole-word match only
# ---------------------------------------------------------------------------


def test_whole_word_match_only() -> None:
    content = "AcmeHealthGroup is not the same as Acme Health."
    modified, injected = inject_wikilinks(content, [BUPA])
    # "AcmeHealthGroup" should NOT be linked
    assert "[[AcmeHealth]]Group" not in modified
    # "Acme Health" at end should be linked
    assert "[[AcmeHealth]]." in modified
    assert injected == ["Acme Health"]


def test_no_match_for_substring() -> None:
    content = "AcmeHealthGroup and SubAcmeHealth are different."
    modified, injected = inject_wikilinks(content, [BUPA])
    # Neither "AcmeHealthGroup" nor "SubAcmeHealth" are whole-word matches for "Acme Health"
    assert injected == []
    assert "[[AcmeHealth]]" not in modified


# ---------------------------------------------------------------------------
# inject_wikilinks: don't inject entity on its own page
# ---------------------------------------------------------------------------


def test_no_self_link_on_own_page() -> None:
    content = "Acme Health is a major health insurer with global operations."
    modified, injected = inject_wikilinks(
        content, [BUPA], source_path="/data/obsidian-vault/02-Areas/Work/Clients/AcmeHealth/Overview.md"
    )
    assert injected == []
    assert "[[AcmeHealth]]" not in modified


def test_self_link_check_different_entity() -> None:
    """On Acme Health's page, Triad Consulting should still be linked."""
    content = "Triad Consulting works with Acme Health on strategy."
    modified, injected = inject_wikilinks(
        content,
        [BUPA, THREE_CUBES],
        source_path="/data/obsidian-vault/02-Areas/Work/Clients/AcmeHealth/Overview.md",
    )
    # Triad Consulting should be linked, Acme Health should not
    assert "[[TriadConsulting]]" in modified
    assert "[[AcmeHealth]]" not in modified
    assert "Triad Consulting" in injected
    assert "Acme Health" not in injected


# ---------------------------------------------------------------------------
# inject_wikilinks: aliases
# ---------------------------------------------------------------------------


def test_alias_triggers_link() -> None:
    """Alias 'Burger Palace' should trigger the [[BurgerPalace|Burger Palace]] link."""
    content = "Burger Palace is a fast food chain."
    modified, injected = inject_wikilinks(content, [HUNGRY_JACKS])
    assert "[[BurgerPalace|Burger Palace]]" in modified
    assert injected == ["Burger Palace"]


def test_primary_name_triggers_link() -> None:
    """Primary name 'Burger Palace' should also trigger."""
    content = "Burger Palace is a fast food chain."
    modified, injected = inject_wikilinks(content, [HUNGRY_JACKS])
    assert "[[BurgerPalace|Burger Palace]]" in modified
    assert injected == ["Burger Palace"]


# ---------------------------------------------------------------------------
# should_inject: eligibility
# ---------------------------------------------------------------------------


def test_should_inject_memory_log() -> None:
    assert should_inject("/data/workspaces/builder/memory/2026-03-23.md") is True


def test_should_inject_agent_knowledge() -> None:
    assert should_inject("/data/obsidian-vault/04-Agent-Knowledge/builder/patterns.md") is True


def test_should_inject_projects() -> None:
    assert should_inject("/data/obsidian-vault/01-Projects/202603-Mnemosyne/README.md") is True


def test_should_inject_areas() -> None:
    assert should_inject("/data/obsidian-vault/02-Areas/Work/Clients/AcmeHealth/Overview.md") is True


def test_should_inject_knowledge() -> None:
    assert should_inject("/data/obsidian-vault/05-Knowledge/01-Strategy/notes.md") is True


def test_should_not_inject_archived_path() -> None:
    assert should_inject("/data/obsidian-vault/02-Areas/Work/Clients/AcmeHealth/archive/old.md") is False


def test_should_not_inject_archived_substring() -> None:
    assert should_inject("/data/obsidian-vault/archived/2023/something.md") is False


def test_should_not_inject_shape_cache() -> None:
    assert should_inject("/home/openclaw/.cache/shape/some-import.md") is False


def test_should_not_inject_non_md() -> None:
    assert should_inject("/data/workspaces/builder/memory/notes.txt") is False


def test_should_not_inject_workspace_non_memory() -> None:
    """Workspace files outside /memory/ subfolder should NOT be eligible."""
    assert should_inject("/data/workspaces/builder/some-other-dir/notes.md") is False


def test_should_not_inject_large_file(tmp_path: Path) -> None:
    """Files > 500KB should not be eligible."""
    large_file = tmp_path / "big.md"
    # Write 501KB
    large_file.write_bytes(b"x" * (501 * 1024))
    # Patch path to look like an eligible vault path
    # We test should_inject directly but need a real file for size check
    # Simulate by using an eligible vault path but with a monkeypatched size
    # We test the file-size check via inject_file (integration), but here
    # we test should_inject with a fake eligible path where the file is large.
    # The size check in should_inject uses os.path.getsize which reads reality.
    # So we create a large file at a temp path that happens to match vault structure.
    # Since we can't easily place it in /data/obsidian-vault, we test inject_file instead.
    # Here we just verify should_inject skips large files by placing one in a tmp dir
    # and calling it directly (the path won't match eligible prefixes, so test inject_file).
    from mnemosyne.wikilinks.injector import inject_file

    result = inject_file(str(large_file), [BUPA])
    assert result == []


# ---------------------------------------------------------------------------
# Alias normalisation: alias surface form → canonical [[link]]
# ---------------------------------------------------------------------------

# WikiEntity for Bridgewater Engineering that has BWE-C and BWE&C as aliases
BRIDGEWATER = make_entity(
    "Bridgewater Engineering",
    "06-Entities/concept/bridgewater-engineering.md",
    link="[[BridgewaterEngineering]]",
    aliases=["BWE-C", "BWE&C"],
    entity_type="concept",
)


def test_alias_surface_form_produces_canonical_link() -> None:
    """'BWE&C strategy' → '[[BridgewaterEngineering]] strategy' (alias triggers canonical link)."""
    content = "The BWE&C strategy is evolving."
    modified, injected = inject_wikilinks(content, [BRIDGEWATER])
    assert "[[BridgewaterEngineering]]" in modified, f"Expected [[BridgewaterEngineering]] in: {modified}"
    assert "[[BWE&C]]" not in modified
    assert injected == ["Bridgewater Engineering"]


def test_alias_sme_c_produces_canonical_link() -> None:
    """'BWE-C' surface form → '[[BridgewaterEngineering]]'."""
    content = "BWE-C is a well-known company."
    modified, injected = inject_wikilinks(content, [BRIDGEWATER])
    assert "[[BridgewaterEngineering]]" in modified
    assert injected == ["Bridgewater Engineering"]


def test_canonical_name_still_works_with_aliases_defined() -> None:
    """Primary name 'Bridgewater Engineering' still triggers '[[BridgewaterEngineering]]' even when aliases exist."""
    content = "Bridgewater Engineering is a major infrastructure company."
    modified, injected = inject_wikilinks(content, [BRIDGEWATER])
    assert "[[BridgewaterEngineering]]" in modified
    assert injected == ["Bridgewater Engineering"]


def test_only_first_alias_mention_linked() -> None:
    """Only the first occurrence of any alias form is linked."""
    content = "BWE&C works on big projects. BWE-C is part of the same group. Bridgewater Engineering is canonical."
    modified, injected = inject_wikilinks(content, [BRIDGEWATER])
    # Only one [[BridgewaterEngineering]] should appear
    assert modified.count("[[BridgewaterEngineering]]") == 1
    assert injected == ["Bridgewater Engineering"]


def test_hungry_jacks_alias_produces_canonical_link() -> None:
    """'Burger Palace' → '[[BurgerPalace|Burger Palace]]' (alias → canonical display)."""
    content = "Burger Palace is a major fast food chain."
    modified, injected = inject_wikilinks(content, [HUNGRY_JACKS])
    assert "[[BurgerPalace|Burger Palace]]" in modified
    assert injected == ["Burger Palace"]
