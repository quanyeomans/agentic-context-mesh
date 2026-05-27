"""F62: every stateful component (tick / batch loop) has a multi-tick idempotency test.

Scope: classes under ``kairix/core/connectors/`` or ``kairix/core/maintenance/``
that expose a method named ``tick``, ``run_batch``, ``run_one_batch``,
``step``, or ``process_batch``. These are the components whose
"good citizen" behaviour depends on a state machine (cursor advance,
work bound per tick) that can't be proved correct by a single-call
test — a regression silently turns "small steady-state work" into
"full rescan every tick".

The matching test must live under ``tests/integration/`` or
``tests/e2e/`` with a name matching one of:
  * ``test_*<snake_name>_advance*.py``
  * ``test_*<snake_name>_multi_tick*.py``
  * ``test_*<snake_name>_idempotency*.py``

(snake_name = the class name lowercased + `_` separator;
e.g. ``ConnectorPipeline`` → ``connector_pipeline``)

Rationale: the v2026.5.28a1 production saturation was caused by
``ConnectorPipeline._commit_and_flush`` writing the wrong cursor value
+ skipping the write on quiet ticks. No multi-tick test existed, so
the regression shipped. F62 forces every new stateful component to
ship its own "tick N+1 with no new input does minimal work" proof.

Class can opt out with a class-level ``# F62-exempt: <rationale>``
comment on the line directly above the class declaration. Use only
when a multi-tick assertion is genuinely meaningless for the class
(e.g. one-shot bootstrap utility).

Spec: ``docs/architecture/fitness-functions.md`` §F62.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate, repo_relative

REMEDIATION = """F62: stateful component <ClassName> has no multi-tick idempotency test.

fix: add tests/integration/test_<snake_name>_advance.py (or _multi_tick or
_idempotency) that runs the component at least twice and asserts tick 2
performs zero / minimal work when no input has changed. See
tests/integration/test_connector_cursor_advance.py for the canonical
shape.
next: see docs/architecture/fitness-functions.md §F62 for the full
specification and exemption rules.
run: python3 scripts/checks/check_f62_stateful_multi_tick.py

Pass example:

    # tests/integration/test_connector_pipeline_advance.py
    @pytest.mark.integration
    def test_quiet_tick_still_advances_cursor(tmp_path):
        pipeline = build_connector_pipeline(...)
        fake = FakeSourceConnector(events_per_tick=[3, 0, 0])
        pipeline.run_batch(fake, FakeExtractor())  # tick 1
        cursor_after_t1 = cursor_store.read("fake")
        pipeline.run_batch(fake, FakeExtractor())  # tick 2 (zero events)
        cursor_after_t2 = cursor_store.read("fake")
        assert cursor_after_t2 == fake.next_cursor()
        assert fake.fetch_call_count == 3  # zero new fetches in tick 2

Forbidden example (current state before F62):

    # tests/integration/test_connector_pipeline.py
    @pytest.mark.integration
    def test_batch_processes_three_events(tmp_path):
        # only one call; doesn't catch the cursor-advance bug
        pipeline.run_batch(fake, FakeExtractor())
        assert result.processed == 3

Allowed exemption:

    # F62-exempt: one-shot bootstrap utility, no tick loop semantics
    class OneShotBootstrap:
        def tick(self) -> None: ...
"""

TICK_METHODS = frozenset({"tick", "run_batch", "run_one_batch", "step", "process_batch"})
SCANNED_ROOTS = ("kairix/core/connectors", "kairix/core/maintenance")
TEST_NAME_PATTERNS = (re.compile(r"^test_.*{name}.*_(advance|multi_tick|idempotency)"),)


def _snake_case(name: str) -> str:
    """Convert ``ConnectorPipeline`` → ``connector_pipeline``."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _line_before(source: str, lineno: int) -> str:
    lines = source.splitlines()
    if lineno < 2 or lineno > len(lines):
        return ""
    return lines[lineno - 2]


def _is_exempt(source: str, class_node: ast.ClassDef) -> bool:
    """A ``# F62-exempt: <rationale>`` on the line above the class declaration exempts it."""
    prior = _line_before(source, class_node.lineno).strip()
    return prior.startswith("# F62-exempt:")


def _has_tick_method(class_node: ast.ClassDef) -> bool:
    for item in class_node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name in TICK_METHODS:
            return True
    return False


def _collect_stateful_classes(repo_root: Path) -> list[tuple[Path, str]]:
    """Return [(source_file, class_name)] for every class with a tick-method."""
    found: list[tuple[Path, str]] = []
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
                if isinstance(node, ast.ClassDef) and _has_tick_method(node) and not _is_exempt(source, node):
                    found.append((path, node.name))
    return found


def _has_matching_test(repo_root: Path, class_name: str) -> bool:
    snake = _snake_case(class_name)
    for tests_root in ("tests/integration", "tests/e2e"):
        root = repo_root / tests_root
        if not root.is_dir():
            continue
        for path in root.rglob("test_*.py"):
            if "__pycache__" in path.parts:
                continue
            stem = path.stem
            for pattern in TEST_NAME_PATTERNS:
                if pattern.search(stem.replace("{name}", snake)) or re.match(
                    rf"^test_.*{snake}.*_(advance|multi_tick|idempotency)", stem
                ):
                    return True
    return False


def main() -> int:
    stateful = _collect_stateful_classes(REPO_ROOT)
    violations: set[Path] = set()
    for source_file, class_name in stateful:
        if not _has_matching_test(REPO_ROOT, class_name):
            violations.add(repo_relative(source_file))
    return gate("f62-stateful-multi-tick", violations, REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
