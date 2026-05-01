"""Step definitions for entity_extraction.feature.

Uses the real entity extraction pipeline against fixture data.
No external API calls.
"""

from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from kairix.knowledge.reflib.extract import scan_reference_library
from kairix.knowledge.reflib.resolve import resolve_entities

pytestmark = pytest.mark.bdd

FIXTURE_ROOT = Path(__file__).resolve().parent.parent.parent / "integration" / "reflib_fixture"

_state: dict = {}


@given('a document titled "Meditations" by Marcus Aurelius')
def meditations_document():
    """The fixture includes philosophy/meditations.md with proper frontmatter."""
    meditations_path = FIXTURE_ROOT / "philosophy" / "meditations.md"
    assert meditations_path.exists(), f"Fixture missing: {meditations_path}"
    _state["reflib_root"] = FIXTURE_ROOT
    _state["entities"] = None
    _state["relationships"] = None


@when("I run entity extraction")
def run_extraction():
    entities, relationships = scan_reference_library(_state["reflib_root"])
    resolved = resolve_entities(entities)
    _state["entities"] = resolved
    _state["raw_entities"] = entities
    _state["relationships"] = relationships


@then(
    'an entity named "Marcus Aurelius" is found',
    target_fixture=None,
)
def entity_marcus_aurelius():
    entities = _state["entities"]
    assert entities is not None, "Entity extraction was not run"
    names = [e.canonical_name for e in entities]
    aliases = []
    for e in entities:
        aliases.extend(e.aliases)
    all_names = names + aliases
    assert any("Marcus Aurelius" in n for n in all_names), (
        f"Expected 'Marcus Aurelius' in entity names, got: {names[:20]}"
    )


@then('an entity of type "Publication" named "Meditations" is found')
def entity_meditations_publication():
    entities = _state["entities"]
    assert entities is not None, "Entity extraction was not run"
    pubs = [e for e in entities if e.entity_type == "Publication"]
    pub_names = [e.canonical_name for e in pubs]
    assert any("Meditations" in n for n in pub_names), f"Expected 'Meditations' publication, got: {pub_names}"


@given("extracted entities include a person and their work")
def entities_with_person_and_work():
    """Run extraction on the full fixture to get person + publication pairs."""
    entities, relationships = scan_reference_library(FIXTURE_ROOT)
    resolved = resolve_entities(entities)
    _state["entities"] = resolved
    _state["raw_entities"] = entities
    _state["relationships"] = relationships


@when("I resolve relationships")
def resolve_relationships():
    # Relationships are already extracted in scan_reference_library
    assert _state["relationships"] is not None, "No relationships extracted"


@then("an AUTHORED_BY edge exists between the work and person")
def authored_by_edge():
    relationships = _state["relationships"]
    authored = [r for r in relationships if r.kind == "AUTHORED_BY"]
    assert len(authored) > 0, (
        f"Expected AUTHORED_BY relationships, found none. All relationship kinds: {set(r.kind for r in relationships)}"
    )
