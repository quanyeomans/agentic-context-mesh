"""kairix.contracts.entities — Entity Resolver Protocol definition."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EntityResolverProtocol(Protocol):
    """Protocol for resolving entity names to IDs and traversing relationships."""

    def resolve(self, name: str) -> str | None:
        """Resolve a human-readable entity name to its canonical entity_id.

        Returns the entity_id string if found, or None if not found.
        """
        ...

    def related(self, entity_id: str, rel_type: str | None = None) -> list[str]:
        """Return a list of entity_ids related to the given entity_id.

        Optionally filtered by relationship type. Returns an empty list
        if no related entities are found.
        """
        ...
