"""
kairix.knowledge.entities.suggest — NER-based entity suggestion.

Uses spaCy (optional) to extract named entities from freetext input,
then cross-references against the Neo4j entity graph to identify which
are already known and which are candidates for new stubs.

Install spaCy support: pip install kairix[nlp]
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SuggestedEntity:
    """A named entity extracted from text and cross-referenced against Neo4j."""

    text: str  # Extracted surface form
    label: str  # spaCy entity label (ORG, PERSON, GPE, etc.)
    existing_id: str | None  # Neo4j entity id if already known, else None
    existing_name: str | None  # Canonical name if already known
    is_new: bool  # True if not found in graph
    context: str = ""  # Surrounding sentence for review


def suggest_entities(text: str, neo4j_client: Any) -> list[SuggestedEntity]:
    """
    Extract named entities from text and cross-reference against Neo4j.

    Args:
        text: Freetext input (document body, meeting notes, etc.)
        neo4j_client: Neo4jClient instance (kairix.knowledge.graph.client.get_client()).
            When unavailable, returns [] with a warning.

    Returns:
        List of SuggestedEntity, deduped by surface form.
        Never raises.
    """
    if not getattr(neo4j_client, "available", False):
        logger.warning("suggest_entities: Neo4j unavailable — returning empty list")
        return []

    try:
        import spacy  # lazy import — optional dependency  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "spaCy is required for entity suggestion. Install it with:\n"
            "  pip install 'kairix[nlp]'\n"
            "  python -m spacy download en_core_web_sm"
        ) from exc

    try:
        nlp = _load_model()
        doc = nlp(text)
    except Exception as exc:
        logger.warning("suggest_entities: spaCy processing failed — %s", exc)
        return []

    # Collect unique surface forms with their labels and context sentences
    seen: dict[str, tuple[str, str]] = {}  # text → (label, context)
    for sent in doc.sents:
        for ent in sent.ents:
            if ent.label_ in {"ORG", "PERSON", "GPE", "PRODUCT", "WORK_OF_ART"}:
                key = ent.text.strip()
                if key and key not in seen:
                    seen[key] = (ent.label_, sent.text.strip()[:200])

    results: list[SuggestedEntity] = []
    for surface_form, (label, context) in seen.items():
        existing_id = None
        existing_name = None
        is_new = True
        try:
            rows = neo4j_client.find_by_name(surface_form)
            if rows:
                existing_id = str(rows[0].get("id", ""))
                existing_name = str(rows[0].get("name", ""))
                is_new = False
        except Exception as exc:
            logger.debug("suggest_entities: Neo4j lookup for %r failed — %s", surface_form, exc)

        results.append(
            SuggestedEntity(
                text=surface_form,
                label=label,
                existing_id=existing_id,
                existing_name=existing_name,
                is_new=is_new,
                context=context,
            )
        )

    return results


def _load_model() -> Any:
    """Load en_core_web_sm model. Raises RuntimeError with install instructions if missing."""
    import spacy

    try:
        return spacy.load("en_core_web_sm")
    except OSError as exc:
        raise RuntimeError(
            "spaCy model 'en_core_web_sm' not found. Install it with:\n  python -m spacy download en_core_web_sm"
        ) from exc


def format_suggestions(suggestions: list[SuggestedEntity], fmt: str = "table") -> str:
    """Format suggestions as a table or JSONL string."""
    if not suggestions:
        return "No entity suggestions found.\n"

    if fmt == "jsonl":
        import dataclasses
        import json

        return "\n".join(json.dumps(dataclasses.asdict(s)) for s in suggestions) + "\n"

    lines = [
        f"{'ENTITY':<35} {'TYPE':<10} {'STATUS':<10} CONTEXT",
        "-" * 100,
    ]
    for s in suggestions:
        status = "existing" if not s.is_new else "NEW"
        name = s.existing_name or s.text
        lines.append(f"{name:<35} {s.label:<10} {status:<10} {s.context[:40]!r}")
    return "\n".join(lines) + "\n"
