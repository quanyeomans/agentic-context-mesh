"""F38: Silver processing only in ``kairix/core/connectors/silver.py``.

Wave 1 of the connector + ingestion plan (see
``docs/architecture/connector-ingestion-architecture.md`` §3 and §5.1)
defines Silver as "the work that turns a Bronze record into
``(chunks, entity_signals)``": chunking + entity-signal extraction.

F38 keeps every chunking primitive — function definitions whose names
match ``chunk_*``, ``_chunk*``, ``tokenize_into_chunks`` — in one
canonical home: ``kairix/core/connectors/silver.py``. No per-connector
chunker, no per-extractor chunker. Connectors and extractors hand
Bronze records to the orchestration layer; the orchestration layer
calls Silver; Silver returns chunks + signals.

Detection — AST walk over every ``.py`` file under ``kairix/``:

  Any module-level or nested ``def`` whose name matches the chunking
  patterns above, sitting outside the allowed roots, is flagged.

The detector is **vacuous-green today** — the existing chunkers under
``kairix/core/temporal/``, ``kairix/core/embed/``, ``kairix/core/search/``,
``kairix/use_cases/``, and ``kairix/corpus/`` are part of the existing
conversational corpus path (orthogonal to the new Bronze→Silver flow
per ADR notes). Those roots are explicitly allowed; the rule fires
only when Wave 1 lands a per-connector or per-extractor chunker.

Allowed locations (chunking welcome here):
  - ``kairix/core/connectors/silver.py`` — canonical Silver surface.
  - ``kairix/quality/probe/**`` — perf-test fixtures.
  - ``kairix/corpus/**`` — existing conversational corpus path.
  - ``kairix/core/temporal/**`` — temporal-chunker (existing).
  - ``kairix/core/embed/**`` — embedding chunker (existing).
  - ``kairix/core/search/**`` — search-time chunk scoring (existing).
  - ``kairix/use_cases/**`` — use-case chunk projections (existing).

Rejected locations (where a chunker is a regression):
  - ``kairix/connectors/<name>/**`` — per-connector chunker.
  - ``kairix/extractors/<name>/**`` — per-extractor chunker.
  - ``kairix/core/connectors/<anything-but-silver.py>`` — orchestration
    code that grew its own chunker.
  - anywhere else under ``kairix/`` not in the allow-list above.

Chunking-function patterns flagged (basename of the ``def``):
  - ``chunk_*`` (``chunk_text``, ``chunk_board``, …)
  - ``_chunk`` / ``_chunk_*`` (private helpers — caught at definition)
  - ``tokenize_into_chunks``
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _fitness_rule import FitnessRule
from tc_fitness import REPO_ROOT

# Regex matching a chunking function name. Anchored at both ends.
_CHUNK_NAME_RE = re.compile(
    r"""
    ^(
        chunk_[a-z0-9_]+        # chunk_text, chunk_board, chunk_file
        | _chunk                # bare _chunk private helper
        | _chunk_[a-z0-9_]+     # _chunk_date_boost_impl, _chunk_to_hit
        | tokenize_into_chunks
    )$
    """,
    re.VERBOSE,
)

# Directories whose contents are explicitly allowed to define chunking
# functions. A file under ANY of these prefixes (relative to repo root)
# is exempt.
_ALLOWED_PREFIXES: tuple[Path, ...] = (
    Path("kairix") / "quality" / "probe",
    Path("kairix") / "corpus",
    Path("kairix") / "core" / "temporal",
    Path("kairix") / "core" / "embed",
    Path("kairix") / "core" / "search",
    Path("kairix") / "use_cases",
)

# The canonical Silver home. Allowed as a single file (not a prefix).
_SILVER_PATH = Path("kairix") / "core" / "connectors" / "silver.py"

REMEDIATION = """Refactor to extract the chunking logic into
kairix/core/connectors/silver.py and call it from your code. Silver is
the single processing surface that takes a Bronze record and returns
(chunks, entity_signals). Per-connector chunkers re-create the format
sprawl the architecture exists to remove — every new source would grow
its own splitter, its own signal extractor, its own off-by-one bug.

fix: extract chunking into kairix/core/connectors/silver.py; call it from your code.
     The connector reads bytes and yields ChangeEvents; the extractor turns bytes
     into an ExtractedDocument; the orchestrator (kairix/core/connectors/pipeline.py)
     calls Silver to produce (chunks, entity_signals).
next: replace the inline chunker with a call into kairix.core.connectors.silver, or
     re-import the existing surface (chunk_text / chunk_file / chunk_board) from the
     conversational-corpus path if the use is orthogonal.
run: python3 scripts/checks/check_f38_silver_singleton.py to confirm green.

Pass example:
  kairix/core/connectors/silver.py             # canonical Silver — allowed
  kairix/core/temporal/chunker.py              # existing conversational corpus — allowed
  kairix/core/embed/embed.py (chunk_text)      # embed-time chunker — allowed
  kairix/quality/probe/chunk_perf_fixture.py   # perf fixture — allowed

Forbidden example:
  kairix/connectors/sharepoint/chunker.py      # F38 — per-connector chunker
  kairix/extractors/markitdown/chunk_page.py   # F38 — per-extractor chunker
  kairix/core/connectors/pipeline.py (chunk_*) # F38 — chunker outside silver.py

Why: see docs/architecture/connector-ingestion-architecture.md §5.1 +
§6. One Silver surface keeps the Bronze→Silver transition uniform across
every source: same chunker, same signal extractor, same boundary, same
defects pinned by tests. Per-connector chunkers fork the surface and
the discipline rots."""


def _is_chunk_function_name(name: str) -> bool:
    """True if ``name`` matches the chunking-function naming pattern."""
    return _CHUNK_NAME_RE.match(name) is not None


def _file_defines_chunk_function(path: Path) -> bool:
    """True if ``path`` defines at least one chunking function.

    Walks the AST and checks every ``FunctionDef`` / ``AsyncFunctionDef``
    node's name — module-level or nested — against the chunking pattern.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_chunk_function_name(node.name):
            return True
    return False


def _is_allowed(rel_path: Path) -> bool:
    """True if ``rel_path`` (repo-relative) is allowed to define chunking
    functions: it sits under one of the allowed prefixes, or it is
    exactly ``kairix/core/connectors/silver.py``.
    """
    if rel_path == _SILVER_PATH:
        return True
    parts = rel_path.parts
    for prefix in _ALLOWED_PREFIXES:
        prefix_parts = prefix.parts
        if len(parts) >= len(prefix_parts) and tuple(parts[: len(prefix_parts)]) == prefix_parts:
            return True
    return False


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Walk every .py file under ``repo_root/kairix/`` and return
    repo-relative paths that define a chunking function AND live
    outside the allowed roots.

    Scoped to ``kairix/`` (the production package). Files under
    ``tests/`` are out of scope — test fixtures can call any chunker
    without growing a parallel Silver surface.
    """
    kairix_dir = repo_root / "kairix"
    if not kairix_dir.exists():
        return set()

    violations: set[Path] = set()
    for path in kairix_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if not _file_defines_chunk_function(path):
            continue
        try:
            rel = path.resolve().relative_to(repo_root)
        except ValueError:
            continue
        if _is_allowed(rel):
            continue
        violations.add(rel)
    return violations


class F38(FitnessRule):
    """F38 as a FitnessRule subclass — see module docstring."""

    name = "f38"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        if not _file_defines_chunk_function(path):
            return False
        rel = self._repo_relative(path)
        return not _is_allowed(rel)


def main() -> int:
    return F38().run()


if __name__ == "__main__":
    sys.exit(main())
