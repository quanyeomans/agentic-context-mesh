"""
kairix.vault.crawler — Vault-to-Neo4j entity crawler (ADR-014).

Derives entity nodes and relationship edges from the natural Obsidian vault
structure, then upserts them into Neo4j Community Edition via Neo4jClient.

Vault structure expected:
  {vault_root}/02-Areas/00-Clients/{Org}/          → OrganisationNode per directory
  {vault_root}/**/Network/People-Notes/             → PersonNode per .md file
  {vault_root}/05-Knowledge/01-Domain-Outcomes/     → OutcomeNode per .md file (optional)
  Wikilinks ([[Name]]) across all .md files         → GraphEdge (MENTIONS)
  Frontmatter: org, role, interests, tier, etc.     → node properties

Designed to run idempotently. Safe to call on every vault sync — Neo4j MERGE
prevents duplicates. Any rename in Obsidian propagates on the next crawl.

Never raises — logs failures and continues. Returns a CrawlReport on completion.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Regex: extract YAML frontmatter block between --- delimiters
_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
# Regex: extract all [[wikilinks]] from text (ignores [[Link|Alias]] alias part)
_WIKILINK_PATTERN = re.compile(r"\[\[([^\]|#]+?)(?:\|[^\]]+?)?\]\]")

# Directory names under 02-Areas to search for People-Notes
_PEOPLE_DIRS = {"People-Notes", "people-notes"}


@dataclass
class CrawlReport:
    """Summary of a vault crawl run."""

    vault_root: str
    dry_run: bool
    organisations_found: int = 0
    persons_found: int = 0
    outcomes_found: int = 0
    edges_found: int = 0
    organisations_upserted: int = 0
    persons_upserted: int = 0
    outcomes_upserted: int = 0
    edges_upserted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def crawl(
    vault_root: str | Path,
    neo4j_client: Any,
    dry_run: bool = False,
) -> CrawlReport:
    """
    Crawl the vault and upsert entity nodes + edges into Neo4j.

    Args:
        vault_root: Absolute path to the Obsidian vault root.
        neo4j_client: An open Neo4jClient instance. Pass a mock for testing.
        dry_run: When True, discover and log entities without writing to Neo4j.

    Returns:
        CrawlReport describing nodes found and upserted.
    """
    from kairix.graph.models import EdgeKind, GraphEdge, OrganisationNode, OutcomeNode, PersonNode

    root = Path(vault_root)
    report = CrawlReport(vault_root=str(root), dry_run=dry_run)

    if not root.exists():
        report.errors.append(f"vault_root does not exist: {root}")
        return report

    # ── 1. Organisation nodes ─────────────────────────────────────────────────
    orgs: dict[str, OrganisationNode] = {}
    clients_dir = root / "02-Areas" / "00-Clients"
    if clients_dir.exists():
        for org_dir in sorted(clients_dir.iterdir()):
            if not org_dir.is_dir():
                continue
            org_id = _to_slug(org_dir.name)
            # Canonical note: {OrgDir}/{OrgDir}.md (index file)
            canonical = org_dir / f"{org_dir.name}.md"
            if not canonical.exists():
                # Fall back to any .md file in the directory
                mds = list(org_dir.glob("*.md"))
                canonical = mds[0] if mds else None

            fm: dict[str, Any] = {}
            if canonical:
                fm = _parse_frontmatter(canonical)

            vault_path = str(canonical.relative_to(root)) if canonical else str(org_dir.relative_to(root))
            node = OrganisationNode(
                id=org_id,
                name=fm.get("name") or _to_display_name(org_dir.name),
                tier=str(fm.get("tier", "client")),
                engagement_status=str(fm.get("engagement_status", "active")),
                vault_path=vault_path,
                industry=_as_list(fm.get("industry")),
                geography=_as_list(fm.get("geography")),
                stakeholder_personas=_as_list(fm.get("stakeholder_personas")),
                aliases=_as_list(fm.get("aliases")),
            )
            orgs[org_dir.name.lower()] = node
            orgs[org_id] = node
            report.organisations_found += 1
            logger.debug("org: %s (%s)", node.name, vault_path)

            if not dry_run:
                if neo4j_client.upsert_organisation(node):
                    report.organisations_upserted += 1
                else:
                    report.errors.append(f"Failed to upsert org: {org_id}")

    # ── 2. Person nodes ───────────────────────────────────────────────────────
    persons: dict[str, PersonNode] = {}
    for people_dir in _find_people_dirs(root):
        for md_file in sorted(people_dir.glob("*.md")):
            person_id = _to_slug(md_file.stem)
            fm = _parse_frontmatter(md_file)
            vault_path = str(md_file.relative_to(root))

            # Resolve org by name lookup in discovered orgs
            org_raw = str(fm.get("org") or fm.get("organisation") or "")
            org_id = _resolve_org_id(org_raw, orgs) if org_raw else ""

            node = PersonNode(
                id=person_id,
                name=fm.get("name") or _to_display_name(md_file.stem),
                org=org_id,
                role=str(fm.get("role") or ""),
                relationship_type=str(fm.get("relationship_type") or "network"),
                last_interaction=str(fm.get("last_interaction") or ""),
                vault_path=vault_path,
                interests=_as_list(fm.get("interests")),
                aliases=_as_list(fm.get("aliases")),
            )
            persons[person_id] = node
            report.persons_found += 1
            logger.debug("person: %s (%s)", node.name, vault_path)

            if not dry_run:
                if neo4j_client.upsert_person(node):
                    report.persons_upserted += 1
                else:
                    report.errors.append(f"Failed to upsert person: {person_id}")

            # WORKS_AT edge when org is known
            if org_id:
                edge = GraphEdge(
                    from_id=person_id,
                    from_label="Person",
                    to_id=org_id,
                    to_label="Organisation",
                    kind=EdgeKind.WORKS_AT,
                )
                report.edges_found += 1
                if not dry_run:
                    if neo4j_client.upsert_edge(edge):
                        report.edges_upserted += 1
                    else:
                        report.errors.append(f"Failed to upsert WORKS_AT edge: {person_id}→{org_id}")

    # ── 3. Outcome nodes (optional) ───────────────────────────────────────────
    outcomes_dir = root / "05-Knowledge" / "01-Domain-Outcomes"
    if outcomes_dir.exists():
        for md_file in sorted(outcomes_dir.rglob("*.md")):
            outcome_id = _to_slug(md_file.stem)
            fm = _parse_frontmatter(md_file)
            vault_path = str(md_file.relative_to(root))

            from kairix.graph.models import OutcomeNode

            node = OutcomeNode(
                id=outcome_id,
                name=fm.get("name") or _to_display_name(md_file.stem),
                domain=str(fm.get("domain") or ""),
                vault_path=vault_path,
            )
            report.outcomes_found += 1
            logger.debug("outcome: %s (%s)", node.name, vault_path)

            if not dry_run:
                if neo4j_client.upsert_outcome(node):
                    report.outcomes_upserted += 1
                else:
                    report.errors.append(f"Failed to upsert outcome: {outcome_id}")

    # ── 4. MENTIONS edges from wikilinks ──────────────────────────────────────
    all_known = set(orgs.keys()) | set(persons.keys())
    for md_file in root.rglob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        source_path = str(md_file.relative_to(root))
        for link_target in _WIKILINK_PATTERN.findall(text):
            target_slug = _to_slug(link_target.split("/")[-1])
            if target_slug not in all_known:
                continue
            # Determine label of target
            if target_slug in orgs:
                to_label, to_id = "Organisation", orgs[target_slug].id
            else:
                to_label, to_id = "Person", persons[target_slug].id

            edge = GraphEdge(
                from_id=_to_slug(md_file.stem),
                from_label="Document",
                to_id=to_id,
                to_label=to_label,
                kind=EdgeKind.MENTIONS,
                props={"source_path": source_path},
            )
            report.edges_found += 1
            if not dry_run:
                if neo4j_client.upsert_edge(edge):
                    report.edges_upserted += 1

    return report


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    """Parse YAML frontmatter from a markdown file. Returns {} on any failure."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    match = _FM_PATTERN.match(text)
    if not match:
        return {}

    try:
        import yaml  # type: ignore[import-untyped]

        result = yaml.safe_load(match.group(1))
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _find_people_dirs(vault_root: Path) -> list[Path]:
    """Find all People-Notes directories under the vault root."""
    found: list[Path] = []
    for candidate in vault_root.rglob("*"):
        if candidate.is_dir() and candidate.name in _PEOPLE_DIRS:
            found.append(candidate)
    return found


def _to_slug(name: str) -> str:
    """Convert a vault directory/file name to a lowercase hyphenated slug."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _to_display_name(name: str) -> str:
    """Convert slug/filename to a display name (title case, hyphens → spaces)."""
    return name.replace("-", " ").replace("_", " ").title()


def _as_list(value: Any) -> list[str]:
    """Normalise a scalar, list, or None frontmatter field to list[str]."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


def _resolve_org_id(org_raw: str, orgs: dict[str, OrganisationNode]) -> str:
    """Find an org id by name or partial match in the discovered orgs dict."""
    from kairix.graph.models import OrganisationNode  # avoid circular at module level

    slug = _to_slug(org_raw)
    if slug in orgs:
        return orgs[slug].id
    # Partial match: org_raw is a substring of a known org name
    org_raw_lower = org_raw.lower()
    for key, node in orgs.items():
        if org_raw_lower in node.name.lower() or org_raw_lower in key:
            return node.id
    return ""
