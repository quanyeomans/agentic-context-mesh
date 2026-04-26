"""CATALOGUE.md and LICENSE-NOTICES.md generation for the reference library."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class CatalogueEntry:
    """One row in the catalogue table."""

    collection: str
    source_name: str
    source_url: str
    licence: str
    licence_tier: int
    file_count: int
    total_size_kb: float
    date_verified: str

    def __str__(self) -> str:
        return f"{self.source_name} ({self.licence})"


def generate_catalogue(entries: list[CatalogueEntry]) -> str:
    """Generate CATALOGUE.md content from catalogue entries."""
    lines: list[str] = [
        "# Reference Library Catalogue",
        "",
        "Every source in this reference library, with licence and verification date.",
        f"Generated: {date.today().isoformat()}",
        "",
        "---",
        "",
    ]

    # Group by collection
    by_collection: dict[str, list[CatalogueEntry]] = {}
    for entry in entries:
        by_collection.setdefault(entry.collection, []).append(entry)

    total_files = 0
    total_size = 0.0

    for collection in sorted(by_collection):
        coll_entries = by_collection[collection]
        coll_files = sum(e.file_count for e in coll_entries)
        coll_size = sum(e.total_size_kb for e in coll_entries)
        total_files += coll_files
        total_size += coll_size

        lines.append(f"## {collection}/ ({coll_files} files, {coll_size:.0f} KB)")
        lines.append("")
        lines.append("| Source | Licence | Files | Size (KB) | Verified | URL |")
        lines.append("|--------|---------|------:|----------:|----------|-----|")

        for e in sorted(coll_entries, key=lambda x: x.source_name):
            lines.append(
                f"| {e.source_name} | {e.licence} | {e.file_count} "
                f"| {e.total_size_kb:.0f} | {e.date_verified} | {e.source_url} |"
            )
        lines.append("")

    # Summary
    lines.insert(6, f"**Total: {total_files} files, {total_size:.0f} KB across {len(by_collection)} collections**")
    lines.insert(7, "")

    return "\n".join(lines) + "\n"


def generate_licence_notices(entries: list[CatalogueEntry]) -> str:
    """Generate LICENSE-NOTICES.md with attribution for T2/T3 sources."""
    lines: list[str] = [
        "# Licence Notices",
        "",
        "Attribution notices for sources included in the kairix reference library.",
        f"Generated: {date.today().isoformat()}",
        "",
        "---",
        "",
        "## Public Domain / CC0 (Tier 1) -- no attribution required",
        "",
    ]

    tier1: list[CatalogueEntry] = []
    tier2: list[CatalogueEntry] = []
    tier3: list[CatalogueEntry] = []

    for e in entries:
        if e.licence_tier == 1:
            tier1.append(e)
        elif e.licence_tier == 2:
            tier2.append(e)
        elif e.licence_tier == 3:
            tier3.append(e)

    for e in sorted(tier1, key=lambda x: x.source_name):
        lines.append(f"- {e.source_name} ({e.licence})")
    lines.append("")

    lines.append("## MIT / Apache 2.0 / MPL (Tier 2) -- attribution required")
    lines.append("")
    for e in sorted(tier2, key=lambda x: x.source_name):
        lines.append(f"### {e.source_name}")
        lines.append(f"- Licence: {e.licence}")
        lines.append(f"- Source: {e.source_url}")
        lines.append(f"- Included under the terms of the {e.licence} licence.")
        lines.append("")

    lines.append("## CC-BY (Tier 3) -- attribution required")
    lines.append("")
    for e in sorted(tier3, key=lambda x: x.source_name):
        lines.append(f"### {e.source_name}")
        lines.append(f"- Licence: {e.licence}")
        lines.append(f"- Source: {e.source_url}")
        lines.append(
            f"- This work is used under the terms of {e.licence}. Attribution: {e.source_name} ({e.source_url})."
        )
        lines.append("")

    return "\n".join(lines) + "\n"
