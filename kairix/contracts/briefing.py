"""kairix.contracts.briefing — Briefing Source Protocol definition."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BriefingSourceProtocol(Protocol):
    """Protocol for any source that can produce briefing items for an agent."""

    def fetch(self, agent: str, limit: int = 10) -> list[dict]:
        """Fetch briefing items for the given agent.

        Returns a list of dicts, each representing one briefing item.
        """
        ...
