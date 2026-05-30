#!/usr/bin/env python3
"""Generate 30 synthetic markdown notes for the per-type fixture corpus.

ADR-028 measurement prereq. Seed-controlled (deterministic content) +
idempotent (overwrites existing fixtures). F32 honoured — generic agent /
project names only.

Run from repo root:
    python3 scripts/reflib/generate_markdown_fixtures.py

Output: reference-library/per-type-fixtures/markdown/*.md (30 files)
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

# Allow direct execution: add repo-root to sys.path so the sibling
# ``_fixture_vocab`` module resolves whether the script is run as
# ``python3 scripts/reflib/generate_markdown_fixtures.py`` from the repo
# root or ``python3 -m scripts.reflib.generate_markdown_fixtures``.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent.parent))

from scripts.reflib._fixture_vocab import (  # noqa: E402 — sys.path tweak above
    AGENTS,
    FISCAL_YEARS,
    PROJECTS,
    QUARTERS,
    TOPICS,
)

SEED = 28028  # ADR-028
N_FIXTURES = 30


def _slugify(text: str) -> str:
    return text.lower().replace(" ", "-")


def _body(rng: random.Random, project: str, topic: str, quarter: str) -> str:
    """Compose a markdown note body with deterministic content."""
    paragraphs = [
        f"# {project.title()} — {topic.title()} ({quarter})",
        "",
        f"This note records the {topic} for {project} as of {quarter}.",
        "",
        "## Context",
        "",
        rng.choice(
            [
                f"The {topic} unblocks the next milestone for {project}.",
                f"{project} has carried this {topic} for the last two cycles.",
                f"The {topic} for {project} is the long pole this period.",
            ]
        ),
        "",
        "## Decisions",
        "",
    ]
    for _ in range(rng.randint(2, 4)):
        agent = rng.choice(AGENTS)
        verb = rng.choice(["proposed", "approved", "deferred", "rejected", "scheduled"])
        decision = rng.choice(TOPICS)
        paragraphs.append(f"- {agent} {verb} the {decision} action for {project}.")
    paragraphs.extend(["", "## Next actions", ""])
    for _ in range(rng.randint(1, 3)):
        agent = rng.choice(AGENTS)
        action = rng.choice(TOPICS)
        paragraphs.append(f"- {agent}: {action} — due next cycle.")
    paragraphs.extend(["", f"_owner: {rng.choice(AGENTS)}_", ""])
    return "\n".join(paragraphs)


def generate(output_dir: Path) -> int:
    """Generate fixtures into ``output_dir`` and return count written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    written = 0
    # Stable iteration order: (project, fiscal_year, quarter, topic)
    combos: list[tuple[str, str, str, str]] = []
    for project, _focus in PROJECTS:
        for fy in FISCAL_YEARS:
            for quarter in QUARTERS:
                for topic in TOPICS:
                    combos.append((project, fy, quarter, topic))
    rng.shuffle(combos)
    for project, fy, quarter, topic in combos[:N_FIXTURES]:
        slug = f"{project}-{_slugify(topic)}-{fy.lower()}-{quarter.lower()}"
        path = output_dir / f"{slug}.md"
        path.write_text(_body(rng, project, topic, f"{fy}-{quarter}"), encoding="utf-8")
        written += 1
    return written


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = repo_root / "reference-library" / "per-type-fixtures" / "markdown"
    n = generate(output_dir)
    print(f"markdown fixtures: wrote {n} files to {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
