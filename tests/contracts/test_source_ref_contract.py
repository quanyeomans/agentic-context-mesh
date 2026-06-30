"""Contract: the shared ``SourceRef`` breadcrumb value object (PLA-274).

``SourceRef`` is the single source-pointer type EVERY agent-facing surface
embeds or returns. This contract pins:

  * the two construction defaults (``source_uri`` falls back to ``path``;
    ``locator`` derived from a ``#<frag>`` on non-paged docs),
  * the lossless ``to_envelope`` / ``from_envelope`` round-trip,
  * behavioural parity — every registered surface row yields a SourceRef
    whose ``source_uri`` is non-empty and resolvable (one parametrised body
    over all surfaces, not per-surface assertions).
"""

from __future__ import annotations

import pytest

from kairix.core.protocols import SourceRef
from kairix.use_cases.contradict import ContradictionHit
from kairix.use_cases.entity_get import EntityGetOutput
from kairix.use_cases.research import ResearchChunk
from kairix.use_cases.search import SearchHit
from kairix.use_cases.timeline import TimelineHit

pytestmark = pytest.mark.contract


# ---------------------------------------------------------------------------
# Construction defaults
# ---------------------------------------------------------------------------


def test_source_uri_falls_back_to_path_when_absent() -> None:
    """A vault note with no connector URI still gets a resolvable pointer.

    Sabotage-proof (executed): changed ``SourceRef.of`` to default
    source_uri to ``""`` — this assertion fired. Restored.
    """
    ref = SourceRef.of(path="notes/onboarding.md")
    assert ref.source_uri == "notes/onboarding.md"
    assert ref.path == "notes/onboarding.md"


def test_explicit_source_uri_is_kept_distinct_from_path() -> None:
    ref = SourceRef.of(path="archive/big.zip#1536", source_uri="sharepoint://site/big.zip")
    assert ref.source_uri == "sharepoint://site/big.zip"
    assert ref.path == "archive/big.zip#1536"


def test_blank_source_uri_falls_back_to_path() -> None:
    """A whitespace-only source_uri is treated as absent."""
    ref = SourceRef.of(path="docs/x.md", source_uri="   ")
    assert ref.source_uri == "docs/x.md"


def test_locator_derived_from_fragment_on_non_paged_doc() -> None:
    """A ``#<seq>`` chunk-key fragment becomes the within-document locator
    when the doc has no page number.

    Sabotage-proof (executed): removed the locator-derivation branch in
    ``SourceRef.of`` — this assertion fired with ``None != '1536'``. Restored.
    """
    ref = SourceRef.of(path="archive/big.zip#1536", source_uri="sharepoint://site/big.zip#1536")
    assert ref.locator == "1536"


def test_paged_doc_keeps_locator_none() -> None:
    """A paged doc cites via ``source_page``; ``locator`` stays None even
    when the path carries a fragment."""
    ref = SourceRef.of(path="report.pdf#3", source_uri="m365://report.pdf", source_page=7)
    assert ref.source_page == 7
    assert ref.locator is None


def test_explicit_locator_is_respected() -> None:
    ref = SourceRef.of(path="notes/x.md", locator="heading-overview")
    assert ref.locator == "heading-overview"


# ---------------------------------------------------------------------------
# Envelope round-trip
# ---------------------------------------------------------------------------


def test_to_from_envelope_round_trip_is_lossless() -> None:
    """``from_envelope(to_envelope(ref)) == ref`` for a fully-populated ref.

    Sabotage-proof (executed): dropped the ``collection`` key from
    ``to_envelope`` — the equality assertion fired. Restored.
    """
    ref = SourceRef(
        source_uri="m365://msg/42",
        path="inbox/msg-42.eml",
        title="Re: deploy",
        collection="mail",
        source_page=None,
        locator="para-3",
    )
    assert SourceRef.from_envelope(ref.to_envelope()) == ref


def test_from_envelope_tolerates_partial_dict() -> None:
    """An older worker that emitted only ``path`` still rebuilds a resolvable
    ref (source_uri falls back to path)."""
    ref = SourceRef.from_envelope({"path": "notes/legacy.md"})
    assert ref.source_uri == "notes/legacy.md"
    assert ref.title is None


def test_from_envelope_of_none_is_empty_ref() -> None:
    ref = SourceRef.from_envelope(None)
    assert ref.source_uri == ""
    assert ref.path == ""


# ---------------------------------------------------------------------------
# Behavioural parity — every surface row yields a resolvable SourceRef
# ---------------------------------------------------------------------------

_CONNECTOR_URI = "sharepoint://acme/handbook.zip"
_CONNECTOR_PATH = "archive/handbook.zip#1536"


def _surface_rows() -> list[tuple[str, object]]:
    """One representative row per registered agent-facing surface, each built
    with a connector source_uri distinct from its display path."""
    return [
        (
            "SearchHit",
            SearchHit(path=_CONNECTOR_PATH, title="H", snippet="s", score=0.5, source_uri=_CONNECTOR_URI),
        ),
        (
            "TimelineHit",
            TimelineHit(path=_CONNECTOR_PATH, title="H", snippet="s", score=0.5, source_uri=_CONNECTOR_URI),
        ),
        (
            "ContradictionHit",
            ContradictionHit(path=_CONNECTOR_PATH, score=0.5, reason="r", snippet="s", source_uri=_CONNECTOR_URI),
        ),
        (
            "EntityGetOutput",
            EntityGetOutput(id="acme", name="Acme", type="Organisation", path="people/acme.md"),
        ),
    ]


@pytest.mark.parametrize("name,row", _surface_rows(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_surface_row_returns_resolvable_source_ref(name: str, row: object) -> None:
    """Parametrised parity: each surface's ``source_ref()`` yields a non-empty,
    resolvable canonical pointer."""
    ref = row.source_ref()  # type: ignore[attr-defined]  # every registered surface exposes source_ref (F97)
    assert isinstance(ref, SourceRef)
    assert ref.source_uri, f"{name}.source_ref().source_uri must be non-empty (resolvable)"


def test_connector_surfaces_surface_canonical_uri_not_path() -> None:
    """For the connector rows, the breadcrumb is the canonical URI, not the
    munged display path."""
    for name, row in _surface_rows():
        if name == "EntityGetOutput":
            continue  # entity uses entity://<id>, asserted below
        ref = row.source_ref()  # type: ignore[attr-defined]  # F97 surface
        assert ref.source_uri == _CONNECTOR_URI, name
        assert ref.path == _CONNECTOR_PATH, name


def test_entity_surface_breadcrumb_is_entity_uri() -> None:
    out = EntityGetOutput(id="acme", name="Acme", type="Organisation", path="people/acme.md")
    assert out.source_ref().source_uri == "entity://acme"


def test_research_chunk_embeds_source_ref() -> None:
    """ResearchChunk uses the EMBED option — a SourceRef-typed ``ref`` field."""
    chunk = ResearchChunk(ref=SourceRef.of(path=_CONNECTOR_PATH, source_uri=_CONNECTOR_URI))
    assert chunk.ref.source_uri == _CONNECTOR_URI
    assert chunk.to_envelope()["source_ref"]["source_uri"] == _CONNECTOR_URI
