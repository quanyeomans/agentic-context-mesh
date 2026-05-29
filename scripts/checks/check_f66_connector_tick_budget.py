"""F66: every connector + tick-driven component declares a per-tick budget.

Scope: every class under ``kairix/connectors/<name>/`` that satisfies the
SourceConnector Protocol shape AND every class under
``kairix/core/connectors/`` or ``kairix/core/maintenance/`` exposing a
``tick`` / ``run_batch`` / ``run_one_batch`` / ``step`` / ``process_batch``
method. Each must declare:

1. **``per_tick_max_items: int``** — class attribute or constructor
   default. Bounds the unit-of-work items the component processes
   before yielding back to the worker loop.

2. **``disk_watermark_min_free_bytes: int | None``** OR a
   ``# F66-watermark-exempt: <rationale>`` comment on the line above
   the class declaration. Components that don't write to disk
   (pure-read query helpers) can be exempt.

Rationale: 2026-05-27 morning incident saw a single tick try to drain
8,783 items in one shot (~14h of work). F66 forces every tick-driven
component to declare its per-tick ceiling + its disk-pressure gate.

Class can opt out of the entire check with a ``# F66-exempt: <rationale>``
comment on the line directly above the class declaration. Use only when
the class is genuinely not tick-driven (e.g. one-shot bootstrap utility
with no recurring invocation).

Spec: ``docs/architecture/ADR-020-connector-tick-budget-watermark.md``.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import (  # noqa: F401 — back-compat for repo_relative usage in _collect_violations
    REPO_ROOT,
    repo_relative,
)
from _fitness_rule import FitnessRule

REMEDIATION = """F66: <ClassName> in <file> does not declare per_tick_max_items
+ disk_watermark_min_free_bytes.

fix: add class-level attributes to the connector or tick-driven component:

    class MyConnector:
        per_tick_max_items: int = 500              # bounded per-tick work
        disk_watermark_min_free_bytes: int | None = None  # None = no gate

The defaults above are safe; tune per-source as Wave E.5 progresses.

next: see ADR-020 for the architectural pattern + sample implementations.

run: python3 scripts/checks/check_f66_connector_tick_budget.py

Pass example:

    class SharePointConnector:
        name = "sharepoint"
        per_tick_max_items: int = 500
        disk_watermark_min_free_bytes: int | None = 5 * 1024**3  # 5 GiB
        ...

    class ObsidianConnector:
        name = "obsidian"
        per_tick_max_items: int = 500
        # F66-watermark-exempt: reads local FS only; no remote-fetch disk pressure
        disk_watermark_min_free_bytes: int | None = None
        ...

Forbidden example:

    class NewConnector:
        # F66 violation: no per-tick budget declared, can drain unbounded items per tick
        name = "newthing"
        ...

Allowed exemption (rare):

    # F66-exempt: one-shot bootstrap utility, never runs in a tick loop
    class OnetimeMigration:
        ...
"""

TICK_METHODS = frozenset({"tick", "run_batch", "run_one_batch", "step", "process_batch"})
REQUIRED_ATTRS = ("per_tick_max_items", "disk_watermark_min_free_bytes")
SCANNED_ROOTS = (
    "kairix/connectors",
    "kairix/core/connectors",
    "kairix/core/maintenance",
)


def _line_before(source: str, lineno: int) -> str:
    lines = source.splitlines()
    if lineno < 2 or lineno > len(lines):
        return ""
    return lines[lineno - 2]


def _is_exempt(source: str, class_node: ast.ClassDef) -> bool:
    prior = _line_before(source, class_node.lineno).strip()
    return prior.startswith("# F66-exempt:")


def _has_watermark_exempt(source: str, class_node: ast.ClassDef) -> bool:
    """The watermark attribute can be exempted independently for pure-read components."""
    prior = _line_before(source, class_node.lineno).strip()
    return prior.startswith("# F66-watermark-exempt:")


def _class_declares_attr(class_node: ast.ClassDef, attr_name: str) -> bool:
    """Class declares a class-level attribute named ``attr_name``.

    Matches both annotated (``per_tick_max_items: int = 500``) and
    plain (``per_tick_max_items = 500``) shapes. Inherited attributes
    from a base class are NOT counted — F66 wants the declaration to
    be explicit at the class that owns the tick semantic.
    """
    for item in class_node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == attr_name:
            return True
        if isinstance(item, ast.Assign):
            for tgt in item.targets:
                if isinstance(tgt, ast.Name) and tgt.id == attr_name:
                    return True
    return False


def _is_tick_driven_or_connector_class(source: str, class_node: ast.ClassDef, file_path: Path) -> bool:
    """A class qualifies for F66 if:
    * It lives under kairix/connectors/<name>/ (every connector plugin), OR
    * It exposes a method named in TICK_METHODS.
    """
    if "kairix/connectors/" in str(file_path):
        # Per-connector check only on the connector's primary class — heuristic:
        # the class has at least a `name: str` attribute AND a `fetch` or `list_changes` method.
        has_name = any(
            isinstance(item, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(t, ast.Name) and t.id == "name"
                for t in (item.targets if isinstance(item, ast.Assign) else [item.target])
            )
            for item in class_node.body
        )
        has_connector_method = any(
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in {"list_changes", "fetch"}
            for item in class_node.body
        )
        if has_name and has_connector_method:
            return True
    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in TICK_METHODS:
            return True
    return False


def _collect_violations(repo_root: Path) -> set[Path]:
    violations: set[Path] = set()
    for rel in SCANNED_ROOTS:
        root = repo_root / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError, OSError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if _is_exempt(source, node):
                    continue
                if not _is_tick_driven_or_connector_class(source, node, path):
                    continue
                if not _class_declares_attr(node, "per_tick_max_items"):
                    violations.add(repo_relative(path))
                    break
                if not _class_declares_attr(node, "disk_watermark_min_free_bytes") and not _has_watermark_exempt(
                    source, node
                ):
                    violations.add(repo_relative(path))
                    break
    return violations


def _file_has_violation(path: Path) -> bool:
    """Per-file predicate — True if any class in this file violates F66."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if _is_exempt(source, node):
            continue
        if not _is_tick_driven_or_connector_class(source, node, path):
            continue
        if not _class_declares_attr(node, "per_tick_max_items"):
            return True
        if not _class_declares_attr(node, "disk_watermark_min_free_bytes") and not _has_watermark_exempt(source, node):
            return True
    return False


class F66(FitnessRule):
    """F66 as a FitnessRule subclass — see module docstring."""

    name = "f66-connector-tick-budget"
    remediation = REMEDIATION
    roots = SCANNED_ROOTS

    def file_has_violation(self, path: Path) -> bool:
        return _file_has_violation(path)


def main() -> int:
    return F66().run()


if __name__ == "__main__":
    sys.exit(main())
