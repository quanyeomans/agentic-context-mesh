"""F5 detector tests — verify private-name access on kairix modules is caught.

The F5 detector (``scripts/checks/check_no_internal_imports.py``) flags
tests that depend on private (``_``-prefixed) names in ``kairix.*``.
Two access shapes are covered:

1. ``ImportFrom`` — ``from kairix.X import _y`` (and renamed variants)
2. ``Attribute`` access on an imported module —
   - ``import kairix.X as alias; alias._y()``
   - ``import kairix.X; kairix.X._y()``
   - ``from kairix import X as alias; alias._y()``

Each shape gets a positive test (private target → violation flagged) and
a negative test (public target → ignored). Tests drive
``file_has_violation`` through the public detector surface; no private
import (and the detector itself is exempt — meta-test).

To add coverage for a new shape: append a positive/negative pair below
and extend the detector to satisfy both. Sabotage-prove by commenting
out the detector branch, running the relevant positive test, confirming
red, restoring, confirming green.
"""

from __future__ import annotations

# Add scripts/checks to sys.path so the detector module is importable.
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from check_no_internal_imports import file_has_violation  # noqa: E402

pytestmark = pytest.mark.unit


def _write_test_module(tmp_path: Path, source: str) -> Path:
    """Write ``source`` to a temporary .py file and return the path."""
    f = tmp_path / "sample_test.py"
    f.write_text(source, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Shape A (existing): ``from kairix.X import _y`` — private name imported
# ---------------------------------------------------------------------------


def test_detects_from_import_of_private_name(tmp_path: Path) -> None:
    """``from kairix.foo import _bar`` is a violation.

    To fix: drive ``_bar`` through the public function/class that calls
    it; if unreachable from the public surface, ``_bar`` is dead code.

    Sabotage-proof: confirmed by removing the ``ImportFrom`` branch in
    the detector — this test then asserts False instead of True and
    fails. Restoring the branch makes it pass again.
    """
    src = """
from kairix.use_cases.eval_suite import _resolve_production_fact_store

_resolve_production_fact_store("/tmp/db.sqlite")
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_allows_from_import_of_public_name(tmp_path: Path) -> None:
    """``from kairix.foo import bar`` is allowed.

    Public names are the explicit boundary; tests can depend on them.
    """
    src = """
from kairix.use_cases.eval_suite import main

main()
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_detects_from_import_of_private_name_with_as_rename(tmp_path: Path) -> None:
    """``from kairix.foo import _bar as bar`` is still a violation.

    The test is depending on the private name's contract regardless of
    what the local binding is called — the rename only obscures intent.
    """
    src = """
from kairix.use_cases.eval_suite import _resolve_production_fact_store as resolve

resolve("/tmp/db.sqlite")
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_detects_import_from_private_submodule(tmp_path: Path) -> None:
    """``from kairix.X._impl import foo`` is a violation — the submodule
    itself is marked private.
    """
    src = """
from kairix.core.search._impl import build_index

build_index()
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


# ---------------------------------------------------------------------------
# Shape B (NEW — broadened detection): attribute access via aliased import
# ---------------------------------------------------------------------------


def test_detects_attribute_access_to_private_via_aliased_import(tmp_path: Path) -> None:
    """``import kairix.X as alias; alias._y()`` is a violation.

    The import is of a public module, but the attribute access reaches
    into the module's private namespace — same coupling shape as a
    direct ``from kairix.X import _y``.

    Sabotage-proof: executed mutate→fail→restore.
    1. Mutate ``_attribute_access_violates`` to ``return False`` always.
    2. Run this test — it fails (asserts True, gets False).
    3. Restore — passes.
    """
    src = """
import kairix.use_cases.eval_suite as alias

alias._resolve_production_fact_store("/tmp/db.sqlite")
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_allows_attribute_access_to_public_via_aliased_import(tmp_path: Path) -> None:
    """``import kairix.X as alias; alias.public_fn()`` is allowed.

    Aliasing the module is a stylistic choice — public attributes are
    still the contract surface.
    """
    src = """
import kairix.use_cases.eval_suite as alias

alias.main()
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_detects_qualified_attribute_access_to_private(tmp_path: Path) -> None:
    """``import kairix.X; kairix.X._y()`` is a violation.

    The fully-qualified access path doesn't change the violation shape;
    the test is still reaching ``kairix.X._y``.

    Sabotage-proof: executed mutate→fail→restore.
    1. Mutate ``_collect_kairix_aliases`` to skip the bare
       ``alias.asname is None`` case (don't bind top-level ``kairix``).
    2. Run this test — fails.
    3. Restore — passes.
    """
    src = """
import kairix.use_cases.eval_suite

kairix.use_cases.eval_suite._resolve_production_fact_store("/tmp/db.sqlite")
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_allows_qualified_attribute_access_to_public(tmp_path: Path) -> None:
    """``import kairix.X; kairix.X.public_fn()`` is allowed."""
    src = """
import kairix.use_cases.eval_suite

kairix.use_cases.eval_suite.main()
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_detects_attribute_access_via_from_import_alias(tmp_path: Path) -> None:
    """``from kairix.use_cases import eval_suite as alias; alias._y()``
    is a violation.

    This is the exact pattern P-Wiring's leftover test uses
    (``from kairix.use_cases import eval_suite as _use_case;
    _use_case._resolve_production_fact_extractor(...)``) and the one
    that motivated broadening F5.

    Sabotage-proof: executed mutate→fail→restore.
    1. Mutate ``_collect_kairix_aliases`` to skip the ``ImportFrom``
       branch.
    2. Run this test — fails.
    3. Restore — passes.
    """
    src = """
from kairix.use_cases import eval_suite as _use_case

_use_case._resolve_production_fact_extractor(fake_llm)
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_allows_attribute_access_via_from_import_to_public(tmp_path: Path) -> None:
    """``from kairix.use_cases import eval_suite as alias; alias.public_fn()``
    is allowed — same as the bare-alias case.
    """
    src = """
from kairix.use_cases import eval_suite as _use_case

_use_case.main(argv=[])
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


# ---------------------------------------------------------------------------
# Edge cases — make sure dunder access and non-kairix aliases are exempt
# ---------------------------------------------------------------------------


def test_allows_dunder_attribute_access(tmp_path: Path) -> None:
    """``alias.__name__`` is allowed — dunders are the language's public
    introspection surface, not implementation detail.
    """
    src = """
import kairix.use_cases.eval_suite as alias

print(alias.__name__)
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_allows_private_attribute_on_non_kairix_module(tmp_path: Path) -> None:
    """``other_lib._private`` is allowed — F5 only governs kairix.*."""
    src = """
import collections as c

c._collections_abc  # noqa: B018 — demonstrating non-kairix attr access
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_allows_private_attribute_on_unbound_name(tmp_path: Path) -> None:
    """Attribute access on a local variable (not an imported module)
    is out of scope — F5 is about test→production private coupling, not
    intra-test data shape.
    """
    src = """
class Box:
    def __init__(self):
        self._private = 1

b = Box()
print(b._private)
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_allows_chained_attribute_access_with_no_private_segment(tmp_path: Path) -> None:
    """``alias.public.nested.public`` is allowed even if deeply chained
    — the rule only fires on a private SEGMENT in the chain.
    """
    src = """
import kairix.core.search as search

search.pipeline.SearchPipeline()
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is False


def test_detects_private_segment_anywhere_in_chain(tmp_path: Path) -> None:
    """``alias._private.something_else`` is a violation — the private
    segment is the moment the test reaches inside.
    """
    src = """
import kairix.use_cases.eval_suite as alias

alias._resolve_production_fact_store.__name__
"""
    assert file_has_violation(_write_test_module(tmp_path, src)) is True


def test_handles_unparseable_file_without_crashing(tmp_path: Path) -> None:
    """If a file has a syntax error, the detector returns False rather
    than crashing — other linters handle syntax errors as their own
    surface.
    """
    src = "def f(:  # syntactically broken\n"
    assert file_has_violation(_write_test_module(tmp_path, src)) is False
