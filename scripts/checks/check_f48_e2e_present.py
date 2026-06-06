"""F48: Composed production path E2E test exists and is e2e-marked.

The Plan-B-parity post-mortem named the failure mode this rule guards
against: 5233 unit/contract/BDD tests passed green while the production-
path LoCoMo benchmark fell to 5%, because no test exercised the composed
production code path (config -> factory.build_* -> ingest -> query ->
assertion) against real SQLite + FTS5 + real factory wiring.

F48 makes that failure mode mechanically impossible to recur by holding
``tests/e2e/test_composed_production_path.py`` as a binary presence
contract: the file must exist, must declare at least one
``@pytest.mark.e2e`` test function, and CI Stage 4.5 (``pytest -m e2e``)
must include it in the e2e selector.

Detection (binary presence, no baseline):

  1. Assert the file ``tests/e2e/test_composed_production_path.py`` exists.
  2. AST-parse it; assert at least one ``FunctionDef`` carries an
     ``@pytest.mark.e2e`` decorator (recognising both
     ``pytest.mark.e2e`` and bare ``e2e`` attribute access where
     ``pytest.mark`` is imported as ``mark``).

CI side (Stage 4.5): ``.github/workflows/ci.yml`` runs
``pytest -m e2e tests/e2e/ -v`` after the integration stage; that job
failing is the second half of this gate (the runtime half).

The check is a *binary* presence/decorator check — there is no baseline
file, because there is no acceptable state in which the exemplar is
absent or unmarked. The canonical shape lives at
``docs/architecture/test-discipline-hardening.md`` §4.3.

Dogfood: this file's own ``REMEDIATION`` carries the F21
``fix:`` / ``next:`` / ``run:`` markers so an agent reading the gate
failure gets the next step without re-deriving it.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The canonical E2E exemplar — its presence + @pytest.mark.e2e marker
# is the entire contract this gate enforces.
EXEMPLAR_REL_PATH = Path("tests/e2e/test_composed_production_path.py")

REMEDIATION = """F48: tests/e2e/test_composed_production_path.py is missing or has no @pytest.mark.e2e test.
next: write the test per docs/architecture/test-discipline-hardening.md §4.3 (canonical E2E shape).
fix: restore tests/e2e/test_composed_production_path.py with at least one @pytest.mark.e2e
     test that exercises config -> factory.build_search_pipeline -> ingest -> query -> assertion.
run: bash scripts/checks/check-f48-e2e-present.sh

Pass example:
  # tests/e2e/test_composed_production_path.py
  @pytest.mark.e2e
  def test_composed_production_path(tmp_path):
      paths = FakePaths(root=tmp_path)
      cfg = load_config(...)
      pipeline = build_search_pipeline(paths=paths, config=cfg)
      ingest(paths=paths, source=FakeSourceConnector(docs=[...]))
      hits = pipeline.run('integration query')
      assert hits and hits[0].score > 0.5

Forbidden example:
  # tests/e2e/test_composed_production_path.py is missing entirely,
  # OR exists but every test function lacks @pytest.mark.e2e — Stage 4.5
  # selector skips them and the LoCoMo-class drift the rule was created
  # to prevent goes undetected."""


def _has_e2e_marker(tree: ast.AST) -> bool:
    """Return True iff any top-level function in ``tree`` carries
    ``@pytest.mark.e2e`` (or the ``@mark.e2e`` alias form).

    Recognises three decorator shapes, all canonical pytest usage:
      * ``pytest.mark.e2e``      — ``Attribute(Attribute(Name(pytest), mark), e2e)``
      * ``mark.e2e``             — ``Attribute(Name(mark), e2e)`` (from ``from pytest import mark``)
      * ``pytestmark = ...e2e``  — module-level marker assignment, less common at function granularity
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for dec in node.decorator_list:
            if _decorator_is_e2e(dec):
                return True
    return False


def _decorator_is_e2e(dec: ast.expr) -> bool:
    """Return True iff ``dec`` is the ``@pytest.mark.e2e`` decorator shape."""
    # ``@pytest.mark.e2e`` -> ``Attribute(value=Attribute(value=Name('pytest'), attr='mark'), attr='e2e')``
    if isinstance(dec, ast.Attribute) and dec.attr == "e2e":
        inner = dec.value
        if isinstance(inner, ast.Attribute) and inner.attr == "mark":
            base = inner.value
            if isinstance(base, ast.Name) and base.id == "pytest":
                return True
        # ``@mark.e2e`` (aliased import) -> ``Attribute(value=Name('mark'), attr='e2e')``
        if isinstance(inner, ast.Name) and inner.id == "mark":
            return True
    return False


def main() -> int:
    """Return 0 if the F48 exemplar is present + e2e-marked; non-zero otherwise.

    Prints a single-line FAIL diagnosis followed by REMEDIATION on
    stderr when the gate fires.
    """
    exemplar = REPO_ROOT / EXEMPLAR_REL_PATH
    if not exemplar.exists():
        print(
            f"FAIL [arch:f48-e2e-present] — {EXEMPLAR_REL_PATH} does not exist.",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    try:
        tree = ast.parse(exemplar.read_text())
    except SyntaxError as e:
        print(
            f"FAIL [arch:f48-e2e-present] — {EXEMPLAR_REL_PATH} could not be parsed: {e}",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    if not _has_e2e_marker(tree):
        print(
            f"FAIL [arch:f48-e2e-present] — {EXEMPLAR_REL_PATH} exists but no function carries @pytest.mark.e2e.",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        return 1

    print("ok [arch:f48-e2e-present] — clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
