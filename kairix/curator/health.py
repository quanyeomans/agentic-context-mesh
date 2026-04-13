"""
kairix.curator.health — Entity graph health check (CA-1).

Primary path: queries Neo4j when a client is provided and available.
Fallback path: checks entities.db for quality and coverage issues:
  - Entity count by type
  - Synthesis failures (entities with no summary)
  - Missing vault_path (entities not linked to canonical vault note)
  - Staleness (entities with no activity for > N days)

Never raises — returns a HealthReport reflecting available data.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

STALENESS_THRESHOLD_DAYS = 90


@dataclass
class HealthIssue:
    """A single entity-level health issue."""

    entity_id: str
    name: str
    entity_type: str
    detail: str


@dataclass
class HealthReport:
    """
    Result of a single Curator health check run.

    All list fields are empty when no issues are found.
    Use report.ok to test overall health at a glance.
    """

    generated_at: str  # ISO UTC timestamp
    total_entities: int
    entities_by_type: dict[str, int]
    synthesis_failures: list[HealthIssue] = field(default_factory=list)
    stale_entities: list[HealthIssue] = field(default_factory=list)
    missing_vault_path: list[HealthIssue] = field(default_factory=list)
    neo4j_available: bool = False
    neo4j_node_counts: dict[str, int] = field(default_factory=dict)
    staleness_threshold_days: int = STALENESS_THRESHOLD_DAYS

    @property
    def issue_count(self) -> int:
        """Total number of entity-level issues found."""
        return len(self.synthesis_failures) + len(self.stale_entities) + len(self.missing_vault_path)

    @property
    def ok(self) -> bool:
        """True when no issues were found."""
        return self.issue_count == 0


def run_health_check(
    db: sqlite3.Connection,
    neo4j_client: Any = None,
    staleness_days: int = STALENESS_THRESHOLD_DAYS,
) -> HealthReport:
    """
    Run a full entity graph health check against entities.db.

    Args:
        db: Open connection to entities.db (schema v2 required).
        neo4j_client: Optional Neo4jClient instance. When provided and its
            ``available`` flag is True, Neo4j node counts are included in the
            report. Pass None to skip Neo4j checks entirely.
        staleness_days: Entities with no activity for this many days are flagged
            as stale. Defaults to STALENESS_THRESHOLD_DAYS (90).

    Returns:
        HealthReport describing the current state of the entity graph.
    """
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    threshold_str = (datetime.now(timezone.utc) - timedelta(days=staleness_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Entity counts ─────────────────────────────────────────────────────────
    total_entities = 0
    entities_by_type: dict[str, int] = {}
    for row in db.execute("SELECT type, COUNT(*) AS cnt FROM entities WHERE status = 'active' GROUP BY type"):
        entities_by_type[row[0]] = row[1]
        total_entities += row[1]

    # ── Synthesis failures ────────────────────────────────────────────────────
    synthesis_failures: list[HealthIssue] = [
        HealthIssue(
            entity_id=row[0],
            name=row[1],
            entity_type=row[2],
            detail="no summary",
        )
        for row in db.execute(
            "SELECT id, name, type FROM entities"
            " WHERE (summary IS NULL OR trim(summary) = '') AND status = 'active'"
            " ORDER BY type, name"
        )
    ]

    # ── Missing vault_path ────────────────────────────────────────────────────
    missing_vault_path: list[HealthIssue] = [
        HealthIssue(
            entity_id=row[0],
            name=row[1],
            entity_type=row[2],
            detail="vault_path not set",
        )
        for row in db.execute(
            "SELECT id, name, type FROM entities"
            " WHERE (vault_path IS NULL OR trim(vault_path) = '') AND status = 'active'"
            " ORDER BY type, name"
        )
    ]

    # ── Stale entities ────────────────────────────────────────────────────────
    # An entity is stale when both last_seen AND updated_at predate the threshold,
    # OR when last_seen is NULL (entity has never appeared in search results) and
    # updated_at predates the threshold.
    # Threshold is computed in Python and passed as a bound parameter — not
    # interpolated into SQL — so no injection risk exists here.
    stale_entities: list[HealthIssue] = [
        HealthIssue(
            entity_id=row[0],
            name=row[1],
            entity_type=row[2],
            detail=f"last seen: {row[3] or 'never'}, last updated: {row[4]}",
        )
        for row in db.execute(
            "SELECT id, name, type, last_seen, updated_at FROM entities"
            " WHERE status = 'active'"
            " AND (last_seen IS NULL OR last_seen < ?)"
            " AND updated_at < ?"
            " ORDER BY type, name",
            (threshold_str, threshold_str),
        )
    ]

    # ── Neo4j node counts (primary) ───────────────────────────────────────────
    # When Neo4j is available, use it as the authoritative entity source and
    # overlay the counts into the report.  entities.db figures remain for
    # deployments that haven't yet migrated to Neo4j-primary.
    neo4j_available = False
    neo4j_node_counts: dict[str, int] = {}
    if neo4j_client is not None:
        try:
            if neo4j_client.available:
                rows = neo4j_client.cypher(
                    "MATCH (n) WHERE labels(n)[0] IN ['Organisation','Person','Outcome'] "
                    "RETURN labels(n)[0] AS label, COUNT(*) AS cnt"
                )
                for r in rows:
                    label = r.get("label")
                    cnt = r.get("cnt")
                    if label is not None and cnt is not None:
                        neo4j_node_counts[str(label)] = int(cnt)
                neo4j_available = True
                # When Neo4j is available, overwrite the entities.db total so
                # the report reflects the canonical graph rather than the stub DB.
                total_entities = sum(neo4j_node_counts.values())
                entities_by_type = {k.lower(): v for k, v in neo4j_node_counts.items()}
        except Exception as exc:
            logger.debug("Neo4j health check failed: %s", exc)

    return HealthReport(
        generated_at=generated_at,
        total_entities=total_entities,
        entities_by_type=entities_by_type,
        synthesis_failures=synthesis_failures,
        stale_entities=stale_entities,
        missing_vault_path=missing_vault_path,
        neo4j_available=neo4j_available,
        neo4j_node_counts=neo4j_node_counts,
        staleness_threshold_days=staleness_days,
    )


def format_report_text(report: HealthReport) -> str:
    """Format a HealthReport as vault-ready Markdown."""
    lines: list[str] = [
        "# Kairix — Entity Health Report",
        f"_Generated: {report.generated_at}_",
        "",
        f"## Entity Counts (active: {report.total_entities})",
        "",
    ]

    if report.entities_by_type:
        lines += [
            "| Type | Count |",
            "|------|-------|",
        ]
        for etype, cnt in sorted(report.entities_by_type.items()):
            lines.append(f"| {etype} | {cnt} |")
    else:
        lines.append("_No active entities._")

    lines += [
        "",
        f"## Synthesis Failures ({len(report.synthesis_failures)})",
        "",
    ]
    if report.synthesis_failures:
        for issue in report.synthesis_failures:
            lines.append(f"- ⚠ `{issue.entity_id}` ({issue.entity_type}) — {issue.detail}")
    else:
        lines.append("✅ None.")

    lines += [
        "",
        f"## Stale Entities ({len(report.stale_entities)}, threshold: {report.staleness_threshold_days} days)",
        "",
    ]
    if report.stale_entities:
        for issue in report.stale_entities:
            lines.append(f"- ⚠ `{issue.entity_id}` ({issue.entity_type}) — {issue.detail}")
    else:
        lines.append("✅ None.")

    lines += [
        "",
        f"## Missing Vault Path ({len(report.missing_vault_path)})",
        "",
    ]
    if report.missing_vault_path:
        for issue in report.missing_vault_path:
            lines.append(f"- ⚠ `{issue.entity_id}` ({issue.entity_type}) — {issue.detail}")
    else:
        lines.append("✅ None.")

    lines += [
        "",
        "## Graph (Neo4j)",
        "",
    ]
    if report.neo4j_available:
        if report.neo4j_node_counts:
            node_summary = ", ".join(f"{cnt} {label}" for label, cnt in sorted(report.neo4j_node_counts.items()))
            lines.append(f"✅ Connected — {node_summary}")
        else:
            lines.append("✅ Connected — no nodes found")
    else:
        lines.append("⚠ Unavailable or not checked.")

    lines += ["", "---"]
    if report.ok:
        lines.append("**Status: ✅ HEALTHY** — no issues found")
    else:
        parts = []
        if report.synthesis_failures:
            parts.append(f"{len(report.synthesis_failures)} synthesis failure(s)")
        if report.stale_entities:
            parts.append(f"{len(report.stale_entities)} stale")
        if report.missing_vault_path:
            parts.append(f"{len(report.missing_vault_path)} missing vault path")
        lines.append(f"**Status: ⚠ ISSUES FOUND** — {', '.join(parts)}")

    return "\n".join(lines) + "\n"


def format_report_json(report: HealthReport) -> str:
    """Format a HealthReport as indented JSON."""
    return json.dumps(dataclasses.asdict(report), indent=2)
