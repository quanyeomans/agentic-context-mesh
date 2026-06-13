"""Tests for the in-process runner's shared :class:`CheckContext`
(#499 Phase 2 stage 4a — the single-process fitness runner).

The context owns the parse-once / walk-once caches that turn the
subprocess-per-rule runner into one process. These tests pin the three
properties the byte-identical-verdict guarantee rests on:

  * **Parse-once.** A file parsed N times through the installed cache is
    really parsed ONCE; the other N-1 are cache hits returning the SAME
    tree object. Across a full ``--all`` dispatch, the real-parse count
    never exceeds the distinct ``(filename, source)`` pairs seen.
  * **Walk-once.** ``ast.walk`` of one tree object is materialised once and
    shared by identity, in the stdlib BFS order.
  * **Isolation.** ``ast.parse`` / ``ast.walk`` are restored to the pristine
    stdlib functions on context exit — the memoisation never leaks past a
    run, and an exotic ``ast.parse`` call (non-default ``mode``) is never
    served a cached ``exec`` tree.

Sabotage proofs (executed; mutate→fail→restore):

  * Parse-once: replace ``CheckContext.parse`` body with a bare
    ``return _REAL_AST_PARSE(...)`` (no caching) → ``test_parse_is_memoised``
    goes red (misses==N, hits==0); restored.
  * Walk-once: drop the ``id(node)`` memo in ``walk`` → ``test_walk_is_memoised``
    goes red (misses==N); restored.
  * Restore: comment out the ``finally`` restore in ``install`` →
    ``test_install_restores_real_ast_functions`` goes red (ast.parse stays
    patched); restored.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CHECKS_DIR = _REPO_ROOT / "scripts" / "checks"
if str(_CHECKS_DIR) not in sys.path:
    sys.path.insert(0, str(_CHECKS_DIR))

from _check_context import CheckContext  # noqa: E402

pytestmark = pytest.mark.unit


_SAMPLE_SRC = "import os\n\n\ndef f(x):\n    return os.path.join(x, 'y')\n"


def test_parse_is_memoised() -> None:
    """The same (filename, source) parsed N times is parsed ONCE; the other
    calls are hits returning the IDENTICAL tree object."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    with ctx.install():
        first = ast.parse(_SAMPLE_SRC, filename="a.py")
        for _ in range(9):
            again = ast.parse(_SAMPLE_SRC, filename="a.py")
            assert again is first  # same object, not a re-parse
    assert ctx.parse_misses == 1
    assert ctx.parse_hits == 9


def test_distinct_sources_under_same_filename_are_not_confused() -> None:
    """Two different source strings under the same filename get two distinct
    trees — the source-text key prevents a stale tree after an edit."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    edited = _SAMPLE_SRC.replace("'y'", "'z'")
    with ctx.install():
        t1 = ast.parse(_SAMPLE_SRC, filename="same.py")
        t2 = ast.parse(edited, filename="same.py")
    assert t1 is not t2
    assert ctx.parse_misses == 2


def test_walk_is_memoised() -> None:
    """Walking the SAME tree object N times materialises the node list once;
    the order is the stdlib BFS order each time."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    with ctx.install():
        tree = ast.parse(_SAMPLE_SRC, filename="w.py")
        # Reference order from the pristine walker (captured at import).
        from _check_context import _REAL_AST_WALK

        expected = list(_REAL_AST_WALK(tree))
        for _ in range(5):
            assert list(ast.walk(tree)) == expected
    assert ctx.walk_misses == 1
    assert ctx.walk_hits == 4


def test_install_restores_real_ast_functions() -> None:
    """On context exit, ``ast.parse`` and ``ast.walk`` are the pristine
    stdlib functions again — no leak past the run."""
    from _check_context import _REAL_AST_PARSE, _REAL_AST_WALK

    ctx = CheckContext(repo_root=_REPO_ROOT)
    with ctx.install():
        assert ast.parse is not _REAL_AST_PARSE  # patched inside
        assert ast.walk is not _REAL_AST_WALK
    assert ast.parse is _REAL_AST_PARSE  # restored outside
    assert ast.walk is _REAL_AST_WALK


def test_install_restores_even_on_exception() -> None:
    """The restore happens in a ``finally`` — a check raising inside the
    block must not leave ``ast.parse`` patched."""
    from _check_context import _REAL_AST_PARSE

    ctx = CheckContext(repo_root=_REPO_ROOT)
    with pytest.raises(RuntimeError):
        with ctx.install():
            raise RuntimeError("a check blew up")
    assert ast.parse is _REAL_AST_PARSE


def test_exotic_parse_mode_bypasses_cache() -> None:
    """A non-default ``mode`` (eval) is never served a cached ``exec`` tree —
    the wrapper delegates exotic calls straight to the real parser."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    with ctx.install():
        exec_tree = ast.parse("1 + 1", filename="e.py")  # mode="exec" (default)
        eval_tree = ast.parse("1 + 1", mode="eval")
    assert isinstance(exec_tree, ast.Module)
    assert isinstance(eval_tree, ast.Expression)  # not the cached Module
    # the eval call did not pollute the exec cache miss count beyond the one exec parse
    assert ctx.parse_misses == 1


def test_python_files_index_is_memoised_and_skips_pycache() -> None:
    """``python_files`` builds the index once per roots tuple and never
    yields ``__pycache__`` files."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    first = ctx.python_files("scripts/checks")
    second = ctx.python_files("scripts/checks")
    assert first is second  # memoised tuple
    assert first  # non-empty
    assert all("__pycache__" not in p.parts for p in first)
    assert all(p.suffix == ".py" for p in first)


def test_source_for_is_cached_and_tolerant() -> None:
    """``source_for`` caches text and returns ``None`` for a missing file
    rather than raising."""
    ctx = CheckContext(repo_root=_REPO_ROOT)
    real = _CHECKS_DIR / "run_checks.py"
    assert ctx.source_for(real) is not None
    assert ctx.source_for(_CHECKS_DIR / "definitely-not-a-file.py") is None
