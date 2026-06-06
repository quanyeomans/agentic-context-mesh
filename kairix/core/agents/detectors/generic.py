"""Generic / harness-agnostic detector (PR 1.3 / #420).

Activates when a candidate directory contains any markdown file matching
one of the common operator-side patterns: a date-stamped journal entry
(``YYYY-MM-DD.md``) or one of the recognised filenames
(``Board.md`` / ``MEMORY.md`` / ``decisions.md`` / ``facts.md`` /
``patterns.md`` / ``rules.md``). The recognised set is deliberately
narrow — wider patterns would over-propose noise.

The detector is NOT gated on "no other harness matched" — that
aggregation logic lives in PR 1.4's ``kairix onboard scan``. Generic
always proposes when its own pattern check succeeds so the aggregator
can dedupe or rank as it sees fit.

Workspace surfaces are never proposed by the generic detector — it has
no harness-specific convention for where workspaces live and refusing
to guess keeps the proposal set predictable for operators.
"""

from __future__ import annotations

import re
from pathlib import Path

from kairix.core.agents.scope import AgentSurface

# Common operator-side memory filenames. Lower-case + canonical-case
# variants matter: ``MEMORY.md`` (shouty) and ``decisions.md`` (lower)
# both ship in the wild.
_NAMED_MARKERS: frozenset[str] = frozenset(
    {
        "Board.md",
        "MEMORY.md",
        "decisions.md",
        "facts.md",
        "patterns.md",
        "rules.md",
    }
)

# Strict YYYY-MM-DD.md — date-stamped journal entry naming convention.
_DATE_PATTERN: re.Pattern[str] = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


class GenericDetector:
    """Harness-agnostic fallback detector."""

    name: str = "generic"

    def propose_surfaces(
        self,
        _agent_name: str,
        candidate_root: Path,
    ) -> tuple[AgentSurface, ...]:
        """Return a memory surface when the directory contains any
        recognised markdown file; otherwise ``()``.

        ``_agent_name`` is unused — the generic detector proposes only
        a memory surface anchored at ``candidate_root``. The parameter
        is preserved for protocol compatibility with the harness-aware
        detectors.
        """
        if not candidate_root.is_dir():
            return ()
        for child in candidate_root.iterdir():
            name = child.name
            if name in _NAMED_MARKERS or _DATE_PATTERN.match(name):
                return (AgentSurface(path=candidate_root, glob="**/*.md", label="memory"),)
        return ()
