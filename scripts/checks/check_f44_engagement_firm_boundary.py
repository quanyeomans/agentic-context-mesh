"""F44: engagement-scope code may not import firm-scope storage clients.

Engagement scope is SQLite + Neo4j + filesystem (this repo, under ``kairix/``).
Firm scope is Postgres-only (a separate codebase). F44 locks the boundary:
engagement code that imports a Postgres client has reached across it. The
detector flags any import whose module name equals or is dotted-under a
denylisted Postgres-client prefix (``psycopg`` / ``psycopg2`` / ``asyncpg`` /
``pg8000`` / ``aiopg``).

Thin shim over :mod:`_import_boundary_engine` (#499 Phase 2). The rule is one
``ImportBoundaryRule`` row in ``prefix`` mode; this module re-exports the
back-compat surface (``collect_violations`` / ``main`` / ``REMEDIATION``) the
F44 unit test loads by file path. The catalogue-driven runner dispatches
``main()`` in-process (#499 Phase 2 stage 4a); the former
``check-f44-engagement-firm-boundary.sh`` delegator wrapper was retired then.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _import_boundary_engine import ImportBoundaryRule, collect_violations_for, register
from tc_fitness import REPO_ROOT, gate

REMEDIATION = """F44: engagement-scope code imports a firm-scope storage client (Postgres).
fix: engagement code talks to SQLite + Neo4j + filesystem only. Firm-scope queries
     belong in the separate firm-scope codebase per the two-scope architecture
     (engagement = SQLite + Neo4j + filesystem; firm = Postgres-only).
next: if you need cross-engagement data, the reflection-extractor is the only sanctioned
      flow. Route through there, not via direct PG access.
run: python3 scripts/checks/check_f44_engagement_firm_boundary.py

Pass example:
  # kairix/core/storage/sqlite_repo.py
  import sqlite3                              # engagement-scope storage — allowed
  from neo4j import GraphDatabase             # engagement-scope graph — allowed

Forbidden example:
  # kairix/core/whatever.py
  import psycopg                              # F44 — firm-scope client in engagement code
  from psycopg2 import connect                # F44 — same boundary violation
  import asyncpg                              # F44 — same boundary violation

Why: the two-scope architecture isolates firm-scope state (cross-engagement
reflections, registry, audit envelope — Postgres-only) into a separate
codebase. Letting engagement code reach into firm storage collapses the
scope boundary."""

RULE = register(
    ImportBoundaryRule(
        name="f44",
        roots=("kairix",),
        mode="prefix",
        forbidden_prefixes=("psycopg", "psycopg2", "asyncpg", "pg8000", "aiopg"),
        remediation=REMEDIATION,
    )
)


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Back-compat surface for the F44 unit test."""
    return collect_violations_for(RULE, repo_root)


def main() -> int:
    return gate(RULE.name, collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
