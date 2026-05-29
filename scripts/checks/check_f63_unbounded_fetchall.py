"""F63: unbounded ``.fetchall()`` calls must declare a scale-bound test or rationale.

Scope: every ``*.py`` file under ``kairix/`` (production code). When
the file contains a ``.fetchall()`` call, the call's SQL string
argument must either (a) include ``LIMIT`` (case-insensitive), or
(b) carry a ``# F63-bounded: <rationale>`` comment on the same
line OR the line immediately above the call.

Rationale: the v2026.5.28a1 production saturation included
``MaintenanceScheduler._prune_orphans`` doing ``fetchall()`` over
989K x 2.1M rows with no LIMIT. At small scale the unbounded scan
was invisible; at production scale it saturated disk IO every tick.
F63 forces every new ``fetchall()`` to either bound the query OR
document why the unbounded scan is safe at the scale it will hit.

The ``# F63-bounded`` comment is for genuinely safe cases like:
  * The query targets a tiny config table (≤100 rows by design).
  * The result set is already bounded by an upstream LIMIT in a CTE.
  * The caller is a one-shot CLI command, not a tick loop.

For genuine scale-sensitive paths (tick loops, background workers),
add ``LIMIT ?`` to the query and a matching test under
``tests/integration/test_*_scale_bound.py`` asserting the per-tick
cap is honoured.

Spec: ``docs/architecture/fitness-functions.md`` §F63.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import python_files, repo_relative  # noqa: F401 — back-compat
from _fitness_rule import FitnessRule

REMEDIATION = """F63: <file> has an unbounded ``.fetchall()`` call.

fix: either add ``LIMIT ?`` to the query and pass a per-tick cap, OR
add a ``# F63-bounded: <rationale>`` comment on the line above the
call explaining why the unbounded scan is safe at production scale.
next: see kairix/core/maintenance/scheduler.py for the per-tick-cap
pattern and tests/integration/test_maintenance_scale_bound.py for the
matching scale-bound test shape.
run: python3 scripts/checks/check_f63_unbounded_fetchall.py

Pass example (bounded query):

    rows = db.execute(
        "SELECT v.hash FROM content_vectors v "
        "LEFT JOIN documents d ON d.hash = v.hash "
        "WHERE d.hash IS NULL LIMIT ?",
        (self._per_tick_cap,),
    ).fetchall()

Pass example (rationale comment):

    # F63-bounded: documents table has < 1000 rows by config schema design.
    rows = db.execute("SELECT * FROM topology_skills").fetchall()

Forbidden example (current state before F63):

    # full-table scan at production scale; no LIMIT, no rationale
    orphans = db.execute(
        "SELECT v.hash FROM content_vectors v "
        "LEFT JOIN documents d ON d.hash = v.hash "
        "WHERE d.hash IS NULL"
    ).fetchall()
"""

LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)
RATIONALE_RE = re.compile(r"#\s*F63-bounded:")


def _find_fetchall_lines(text: str) -> list[int]:
    """Return 1-based line numbers of every ``.fetchall()`` call site in ``text``."""
    out: list[int] = []
    for i, line in enumerate(text.splitlines(), start=1):
        # crude but adequate: detect ``.fetchall()`` literal anywhere on the line.
        # We don't AST-parse because the SQL string we want to inspect spans
        # multiple lines in most call sites — easier to scan backwards from
        # the .fetchall() literal to find LIMIT or rationale.
        if ".fetchall()" in line:
            out.append(i)
    return out


def _context_for_call(lines: list[str], call_line_1based: int, lookback: int = 12) -> str:
    """Return the call site + the ``lookback`` preceding lines as one string.

    Captures multi-line ``db.execute("SELECT ... LIMIT ?", (...)) .fetchall()``
    patterns where LIMIT is several lines before the .fetchall() literal.
    """
    start = max(0, call_line_1based - 1 - lookback)
    end = call_line_1based
    return "\n".join(lines[start:end])


def file_violates(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    fetchall_lines = _find_fetchall_lines(text)
    if not fetchall_lines:
        return False
    lines = text.splitlines()
    for ln in fetchall_lines:
        context = _context_for_call(lines, ln)
        # Acceptable if either LIMIT in the query OR rationale comment in window.
        if LIMIT_RE.search(context) or RATIONALE_RE.search(context):
            continue
        return True
    return False


class F63(FitnessRule):
    """F63 as a FitnessRule subclass — see module docstring."""

    name = "f63-unbounded-fetchall"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return file_violates(path)


def main() -> int:
    return F63().run()


if __name__ == "__main__":
    sys.exit(main())
