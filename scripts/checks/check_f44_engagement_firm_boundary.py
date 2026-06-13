"""F44: engagement-scope code may not import firm-scope storage clients.

The two-scope architecture splits the world in two:

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
at pre-commit. Closes the boundary the two-scope architecture names.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import REPO_ROOT, repo_relative  # noqa: F401 — kept for back-compat with importing tests

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
     belong in the separate firm-scope codebase per the two-scope architecture
     (engagement = SQLite + Neo4j + filesystem; firm = Postgres-only).
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

Why: the two-scope architecture isolates firm-scope state (cross-engagement
reflections, registry, audit envelope — Postgres-only) into a separate
codebase. Letting engagement code reach into firm storage collapses the
scope boundary."""


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


class F44(FitnessRule):
    """F44 as a FitnessRule subclass — see module docstring for rule semantics."""

    name = "f44"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return file_has_violation(path)


# Public ``collect_violations`` kept for back-compat with any code that
# imports it directly (tests, scripts). The class is the canonical entry.
def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    return F44(repo_root=repo_root).collect_violations()


def main() -> int:
    return F44().run()


if __name__ == "__main__":
    sys.exit(main())
