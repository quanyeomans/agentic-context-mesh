#!/usr/bin/env python3
"""Regenerate the catalogue-derived doc regions from _rule_catalogue.py.

Single source of truth (EPIC #499 Phase 2). The rule listing in
``docs/architecture/fitness-functions.md`` and the grouped catalogue
section in ``CLAUDE.md`` are DERIVED from
``scripts/checks/_rule_catalogue.py`` — never hand-edited. Each lives
between explicit markers:

    <!-- BEGIN F-CATALOGUE (generated; edit _rule_catalogue.py) -->
    ...generated...
    <!-- END F-CATALOGUE -->

Run it to regenerate in place (idempotent):

    python3 scripts/checks/generate_catalogue_docs.py

Run it in ``--check`` mode (used by the F85 currency gate) to fail when
the on-disk regions drift from what the catalogue would generate:

    python3 scripts/checks/generate_catalogue_docs.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _rule_catalogue import ALL_ENTRIES, RuleEntry, categories_in_use

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

BEGIN = "<!-- BEGIN F-CATALOGUE (generated; edit _rule_catalogue.py) -->"
END = "<!-- END F-CATALOGUE -->"

FITNESS_DOC = REPO_ROOT / "docs" / "architecture" / "fitness-functions.md"
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"

# Human-readable category headings for the grouped CLAUDE.md section,
# keyed by the catalogue's Category Literal. Order is driven by
# categories_in_use() (first-occurrence order in the catalogue).
_CATEGORY_HEADINGS: dict[str, str] = {
    "layering": "Layering",
    "test-discipline": "Test discipline",
    "plugin-contract": "Plugin contract",
    "production-safety": "Production safety",
    "schema-integrity": "Schema integrity",
    "feature-flag": "Feature flag",
    "agent-affordance": "Agent affordance",
    "repo-hygiene": "Repo hygiene",
    "observability": "Observability",
    "coverage": "Coverage",
    "process": "Process",
    "go-discipline": "Go side",
}


def _generatable(entry: RuleEntry) -> bool:
    """Include real F-numbered/named rules; skip nothing — proposed
    rules are documented too (their status is surfaced)."""
    return True


def _status_suffix(entry: RuleEntry) -> str:
    """A short ``(status)`` marker for non-shipped rules, else empty."""
    return "" if entry.status == "shipped" else f" _({entry.status})_"


def render_claude_section() -> str:
    """The grouped, per-category bullet listing for CLAUDE.md.

    One subsection per category (in catalogue declaration order), each a
    single paragraph of ``**ID** summary.`` bullets — the shape the
    hand-written section had, now derived."""
    lines: list[str] = [BEGIN, ""]
    for category in categories_in_use():
        heading = _CATEGORY_HEADINGS.get(category, category.replace("-", " ").title())
        entries = [e for e in ALL_ENTRIES if e.category == category and _generatable(e)]
        if not entries:
            continue
        lines.append(f"**{heading}**")
        bullets = [f"**{e.id}** {e.summary}{_status_suffix(e)}" for e in entries]
        lines.append("- " + " ".join(b if b.endswith(".") else b + "." for b in bullets))
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def render_fitness_listing() -> str:
    """A machine-derived table for fitness-functions.md: every catalogue
    rule with its category, scope, status, and summary — the canonical
    index that tracks the catalogue exactly."""
    lines: list[str] = [BEGIN, ""]
    lines.append("_Generated from `scripts/checks/_rule_catalogue.py` — do not edit by hand._")
    lines.append("")
    lines.append("| ID | Category | Scope | Status | Summary |")
    lines.append("|----|----------|-------|--------|---------|")
    for e in ALL_ENTRIES:
        if not _generatable(e):
            continue
        summary = e.summary.replace("|", "\\|")
        lines.append(f"| {e.id} | {e.category} | {e.scope} | {e.status} | {summary} |")
    lines.append("")
    lines.append(END)
    return "\n".join(lines)


def _splice(doc_text: str, generated: str) -> str:
    """Replace the region between BEGIN/END markers with ``generated``.

    Raises ``ValueError`` if the markers are missing or malformed —
    the doc must already carry the marker pair (a one-time manual
    insertion)."""
    if BEGIN not in doc_text or END not in doc_text:
        raise ValueError(f"markers {BEGIN!r} / {END!r} not found — insert the marker pair once, then regenerate")
    head, _, rest = doc_text.partition(BEGIN)
    _, _, tail = rest.partition(END)
    return f"{head}{generated}{tail}"


def _targets() -> list[tuple[Path, str]]:
    """The (path, generated-region) pairs this generator owns."""
    return [
        (FITNESS_DOC, render_fitness_listing()),
        (CLAUDE_MD, render_claude_section()),
    ]


def regenerate() -> None:
    """Write the generated regions in place (idempotent)."""
    for path, generated in _targets():
        text = path.read_text(encoding="utf-8")
        path.write_text(_splice(text, generated), encoding="utf-8")


def check() -> int:
    """Return 0 iff every on-disk region matches what the catalogue
    would generate; print the drifting files and return 1 otherwise."""
    drift: list[Path] = []
    for path, generated in _targets():
        text = path.read_text(encoding="utf-8")
        expected = _splice(text, generated)
        if expected != text:
            drift.append(path)
    if drift:
        print("doc regions drift from the catalogue:")
        for path in drift:
            print(f"  {path.relative_to(REPO_ROOT)}")
        print()
        print("fix: run `python3 scripts/checks/generate_catalogue_docs.py` to regenerate,")
        print("then stage the updated docs.")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate_catalogue_docs.py")
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the on-disk regions drift from the catalogue (no writes)",
    )
    args = parser.parse_args(argv)
    if args.check:
        return check()
    regenerate()
    print("regenerated catalogue doc regions in:")
    for path, _ in _targets():
        print(f"  {path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
