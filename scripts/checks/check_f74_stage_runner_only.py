"""F74: every Stage subclass is only invoked via a StageRunner.

ADR-026 §A.5 promised: once the ``Stage`` / ``StageRunner`` abstraction
lands (commit ``8478fcf1``), production code must route every stage
invocation through a runner — never via direct ``stage.process(ctx)``
call. The runner is what guarantees the per-call ``status_emit``,
exception classification, and (for ``BatchTransactionStageRunner``)
the per-item dead-letter / batch-critical-rollback semantics. A
direct call bypasses all three.

Detection (AST)
---------------
Walk every ``.py`` file under ``kairix/``. Flag any call expression
``<recv>.process(<ctx>)`` where:

* the receiver name contains ``"stage"`` (case-insensitive) — e.g.
  ``self._fetch_stage``, ``stage``, ``self.silver_stage``; AND
* the call site is NOT inside a class body whose name ends in
  ``StageRunner`` (the runner is the only legitimate invoker).

The heuristic deliberately keys on the receiver name. Today no
``*Stage`` subclasses exist (Track A.3 / A.4 haven't landed), so the
detector is **vacuous-green**: zero violations, no baseline needed.
Fires the moment the first Stage migration lands a direct
``<x>.process(ctx)`` call outside a runner.

False-positive avoidance
------------------------
The pre-existing call ``self._silver.process(ref, doc, ...)`` in
``kairix/core/connectors/pipeline.py`` does NOT trip F74 — ``silver``
doesn't contain the substring ``stage``. ``SilverProcessor`` is a
Protocol with its own ``process`` method; it predates the Stage
abstraction and is structurally distinct.

If a future component is genuinely named something like
``stage_router`` but isn't a Stage subclass, mark it with a
``# F74-exempt: <rationale>`` comment on the line directly above the
``.process(...)`` call.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import python_files, repo_relative  # noqa: F401 — back-compat
from _fitness_rule import FitnessRule

REMEDIATION = """F74: direct stage.process(ctx) call outside a StageRunner — the runner is
the only legitimate invoker. Direct calls bypass status_emit, exception
classification, and the dead-letter / batch-rollback semantics that the
runner provides.

fix: route the call through IsolatedStageRunner (maintenance / soft-failure
  semantics) or BatchTransactionStageRunner (connector pipeline / hard
  semantics with dead-letter + batch rollback). The runner takes the
  stage in its constructor and exposes .run(ctx) / .run_per_item(ctx) /
  .run_batch_critical(ctx) methods. See kairix/core/observability/stage.py
  for the full API.
next: re-run python3 scripts/checks/check_f74_stage_runner_only.py to
  confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(<area>): route <stage> through StageRunner"

Pass example:
  # kairix/core/connectors/pipeline.py
  from kairix.core.observability.stage import BatchTransactionStageRunner

  runner = BatchTransactionStageRunner(fetch_stage, db=db, dead_letter=dead_letter)
  outcome = runner.run_per_item(ctx)

Forbidden example:
  # kairix/core/connectors/pipeline.py
  fetch_stage = FetchStage(connector=connector)
  outcome = fetch_stage.process(ctx)  # F74 — bypasses runner

Allowed exemption (rare):
  # F74-exempt: stage_router is the dispatcher, not a Stage subclass
  outcome = stage_router.process(ctx)

Why: see docs/architecture/ADR-026-cross-cutting-primitive-abstractions.md
§4 — the Stage/StageRunner split exists to centralise cross-cutting
concerns (emit, classify, transaction semantics). Per-call-site
hand-rolling reintroduces the failure modes the abstraction prevents."""

_EXEMPT_COMMENT = "# F74-exempt:"


def _receiver_name_hint(call: ast.Call) -> str | None:
    """Return the attribute name on the receiver of a ``<recv>.process(...)`` call.

    Handles ``self._stage.process()`` (Attribute receiver),
    ``stage.process()`` (Name receiver), and skips anything else
    (subscript, call expression, etc.).
    """
    if not isinstance(call.func, ast.Attribute):
        return None
    recv = call.func.value
    if isinstance(recv, ast.Attribute):
        return recv.attr
    if isinstance(recv, ast.Name):
        return recv.id
    return None


def _enclosing_class_ranges(tree: ast.Module, name_predicate) -> list[tuple[int, int]]:
    """Return (start_lineno, end_lineno) for every class whose name matches the predicate.

    A call on line L is "inside" a matching class iff L falls within
    one of the returned ranges.
    """
    out: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and name_predicate(node.name):
            end = node.end_lineno or node.lineno
            out.append((node.lineno, end))
    return out


def _line_before(source: str, lineno: int) -> str:
    lines = source.splitlines()
    if lineno < 2 or lineno > len(lines):
        return ""
    return lines[lineno - 2]


def _file_has_violation(path: Path) -> bool:
    """True if any direct ``<*stage*>.process(...)`` call sits outside a
    ``*StageRunner`` class body and lacks an F74-exempt rationale.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return False

    runner_ranges = _enclosing_class_ranges(tree, lambda n: n.endswith("StageRunner"))

    def inside_runner(line: int) -> bool:
        return any(start <= line <= end for start, end in runner_ranges)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "process":
            continue
        recv = _receiver_name_hint(node)
        if recv is None or "stage" not in recv.lower():
            continue
        if inside_runner(node.lineno):
            continue
        prior = _line_before(source, node.lineno).strip()
        if prior.startswith(_EXEMPT_COMMENT):
            continue
        return True
    return False


class F74(FitnessRule):
    """F74 as a FitnessRule subclass — see module docstring."""

    name = "f74-stage-runner-only"
    remediation = REMEDIATION
    roots = ("kairix",)

    def file_has_violation(self, path: Path) -> bool:
        return _file_has_violation(path)


def main() -> int:
    return F74().run()


if __name__ == "__main__":
    sys.exit(main())
