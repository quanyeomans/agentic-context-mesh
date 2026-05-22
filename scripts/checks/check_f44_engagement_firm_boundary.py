"""F44: engagement-scope code may not import firm-scope storage clients.

The two-scope architecture (kairix-pro-platform
``docs/ADRs/ADR-017-two-scope-architecture.md`` +
``ADR-018-storage-tiering.md``) splits the world in two:

  * **Engagement scope** — single-engagement state. SQLite + Neo4j +
    filesystem. Lives in this repo, under ``kairix/``.
  * **Firm scope** — cross-engagement state (reflections, registry,
    audit envelope). Postgres-only. Lives in the separate
    ``kairix-firm/`` codebase.

F44 locks the boundary mechanically. Engagement-scope code MUST NOT
import a Postgres client; doing so means engagement code has reached
across the scope boundary and is talking directly to firm-scope
storage. The only sanctioned cross-scope flow is the
reflection-extractor (one-way, append-only, audited).

Denylist (the standard Python Postgres clients):
  - ``psycopg``
  - ``psycopg2``
  - ``asyncpg``
  - ``pg8000``
  - ``psycopg-binary``
  - ``psycopg2-binary``
  - ``aiopg``

(Distribution names like ``psycopg-binary`` are listed for
completeness, even though Python ``import`` statements name the
underlying module — the AST scanner only sees the importable form,
e.g. ``import psycopg`` or ``from psycopg2 import connect``. We match
on import-name prefixes so submodule imports such as
``psycopg2.extras`` also fire.)

In-scope tree: every ``.py`` file under ``kairix/`` (the production
package). Test files under ``tests/`` are out of scope — fakes and
fixtures don't ship in the wheel. ``kairix-firm/`` would also be out
of scope (different codebase), but it does not exist in this repo
today.

Today: zero firm-scope code exists in ``kairix/``; the denylist
matches nothing. F44 is preventive — it locks the boundary so the
first attempt to ``import psycopg`` from engagement code is blocked
at pre-commit. Closes the boundary kairix-pro-platform ADR-017 names
architecturally.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate, repo_relative

# Forbidden Postgres-client import-name prefixes. An import is flagged
# if the imported module name equals one of these OR starts with one
# followed by ``.`` (so ``psycopg2.extras`` fires the same way
# ``psycopg2`` does).
_FIRM_SCOPE_CLIENT_PREFIXES: frozenset[str] = frozenset(
    {
        "psycopg",
        "psycopg2",
        "asyncpg",
        "pg8000",
        # psycopg-binary / psycopg2-binary are wheel distributions that
        # install the psycopg / psycopg2 module respectively — covered
        # by the entries above. Listed in the module docstring for the
        # reader; not duplicated here because they're not importable
        # names.
        "aiopg",
    }
)

REMEDIATION = """F44: engagement-scope code imports a firm-scope storage client (Postgres).
fix: engagement code talks to SQLite + Neo4j + filesystem only. Firm-scope queries
     belong in kairix-firm/ (separate codebase) — see kairix-pro-platform
     docs/ADRs/ADR-017-two-scope-architecture.md and ADR-018-storage-tiering.md.
next: if you need cross-engagement data, the reflection-extractor is the only sanctioned
      flow. Route through there, not via direct PG access.
run: bash scripts/checks/check-f44-engagement-firm-boundary.sh

Pass example:
  # kairix/core/storage/sqlite_repo.py
  import sqlite3                              # engagement-scope storage — allowed
  from neo4j import GraphDatabase             # engagement-scope graph — allowed

Forbidden example:
  # kairix/core/whatever.py
  import psycopg                              # F44 — firm-scope client in engagement code
  from psycopg2 import connect                # F44 — same boundary violation
  import asyncpg                              # F44 — same boundary violation

Why: see kairix-pro-platform docs/ADRs/ADR-017-two-scope-architecture.md.
Firm scope (cross-engagement reflections, registry, audit) is
Postgres-only and lives in a separate codebase. Letting engagement
code reach into firm storage collapses the scope boundary the ADR
exists to enforce."""


def _imported_names(tree: ast.AST) -> list[str]:
    """Yield every module name referenced by an ``Import`` or
    ``ImportFrom`` node in ``tree``.

    For ``import foo.bar`` we return ``foo.bar`` (the full dotted
    name). For ``from foo.bar import baz`` we return ``foo.bar``.
    ``from . import x`` (relative imports, ``module is None``) is
    skipped — relative imports cannot reach an external package.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name:
                    names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


def _is_firm_scope_client(module: str) -> bool:
    """True if ``module`` names (or has as a prefix) a denylisted
    Postgres client.

    Matches on exact name (``import psycopg``) or dotted prefix
    (``from psycopg2.extras import ...`` → ``psycopg2.extras`` has
    prefix ``psycopg2``).
    """
    for prefix in _FIRM_SCOPE_CLIENT_PREFIXES:
        if module == prefix or module.startswith(prefix + "."):
            return True
    return False


def file_has_violation(path: Path) -> bool:
    """True if ``path`` imports any denylisted Postgres client."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for module in _imported_names(tree):
        if _is_firm_scope_client(module):
            return True
    return False


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Walk every ``.py`` file under ``<repo_root>/kairix/`` and
    return repo-relative paths of files that import a denylisted
    Postgres client. Empty set if ``kairix/`` does not exist.
    """
    kairix_dir = repo_root / "kairix"
    if not kairix_dir.exists():
        return set()

    violations: set[Path] = set()
    for path in kairix_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if file_has_violation(path):
            try:
                violations.add(path.resolve().relative_to(repo_root))
            except ValueError:
                violations.add(repo_relative(path))
    return violations


def main() -> int:
    violations = collect_violations()
    return gate("f44", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
