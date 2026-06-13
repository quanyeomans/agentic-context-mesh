"""Shared per-run state for the in-process fitness runner (#499 Phase 2, stage 4a).

Motivation
----------
The catalogue-driven runner used to spawn one cold subprocess per rule
(~80 processes). Across those processes the same ``kairix/**`` and
``tests/**`` trees were ``ast.parse``-d dozens of times — ~56 AST checks
each re-parsed the same files, and ~71 re-imported ``tc_fitness``. A full
``--all`` run was ~36s, almost all of it cold-start + redundant parsing.

This module supplies a single :class:`CheckContext` that the runner builds
ONCE per process and the checks share:

* a ``python_files(scope)`` index — each relevant tree is ``rglob``-d once;
* an ``ast.parse`` cache keyed by ``(filename, source-text)`` so every file
  is parsed AT MOST ONCE per run, no matter how many rules inspect it;
* a source-text cache for the regex / text checks.

Parse-once mechanism
--------------------
The dominant cost is ``ast.parse``. Every AST-based check — whether it goes
through ``_import_boundary_engine._parse``, ``_location_engine._parse``, a
``FitnessRule`` subclass's inline ``ast.parse``, or a standalone check — calls
the stdlib ``ast.parse(source, filename=...)``. Rather than thread a context
object through every one of ~56 call sites (high blast radius, high risk of a
verdict-changing edit), :func:`install_parse_cache` swaps ``ast.parse`` for a
memoising wrapper for the lifetime of an in-process run. The cache key is
``(filename, source-text)``: identical source for the same file yields the
identical tree, and the checks only ever ``ast.walk`` / read trees (never
mutate them), so sharing one parsed tree across rules is safe and observably
identical to re-parsing. The original ``ast.parse`` is restored on context
exit, so the patch never leaks past a run.

The cache key folds in the source TEXT (not just mtime) so a file edited
mid-run — or two distinct files that happen to share a stat — can never serve
a stale tree. The ``(path, st_mtime_ns)`` index the runner also keeps is a
cheap pre-read dedupe; correctness rests on the source-text key.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# The pristine stdlib parser + walker, captured at import time. Every cached
# parse / walk delegates to THESE, never to the (possibly-already-patched)
# ``ast.parse`` / ``ast.walk`` names, so nesting installs can't recurse.
_REAL_AST_PARSE = ast.parse
_REAL_AST_WALK = ast.walk


class CheckContext:
    """One-per-run shared index + parse/source caches.

    The runner constructs a single ``CheckContext`` and installs its parse
    cache for the duration of an ``--all`` / ``--staged`` / ``--gate`` run.
    Accessors are intentionally small and read-only; the heavy lifting is the
    ``ast.parse`` memoisation installed via :meth:`install`.
    """

    def __init__(self, repo_root: Path | None = None) -> None:
        self._repo_root = (repo_root if repo_root is not None else Path.cwd()).resolve()
        # filename(str) -> (source-text -> parsed tree). Two-level so a file
        # rewritten between parses keys on its new text, never a stale tree.
        self._tree_cache: dict[str, dict[str, ast.AST]] = {}
        self._source_cache: dict[Path, str | None] = {}
        self._files_cache: dict[tuple[str, ...], tuple[Path, ...]] = {}
        # id(tree) -> fully-materialised node list (the ``ast.walk`` order).
        # Keyed by object identity: the parse cache returns the SAME tree
        # object for the same file, so every rule's walk of that tree reuses
        # one traversal. Cleared with the context (no cross-run leak).
        self._walk_cache: dict[int, list[ast.AST]] = {}
        # Instrumentation: real parses (cache misses) vs cache hits. The
        # parse-once test asserts misses ≤ distinct-files and hits > 0.
        self.parse_misses = 0
        self.parse_hits = 0
        self.walk_misses = 0
        self.walk_hits = 0

    # ── repo anchor ──────────────────────────────────────────────────────

    @property
    def repo_root(self) -> Path:
        """The repository root every scope/relativisation resolves against."""
        return self._repo_root

    # ── file index ───────────────────────────────────────────────────────

    def python_files(self, *roots: str) -> tuple[Path, ...]:
        """Every ``.py`` file under each of ``roots`` (repo-relative dirs),
        skipping ``__pycache__``. Built once per distinct ``roots`` tuple and
        memoised — the same index the per-rule walks would each rebuild."""
        key = tuple(roots)
        cached = self._files_cache.get(key)
        if cached is not None:
            return cached
        out: list[Path] = []
        for rel in roots:
            base = self._repo_root / rel
            if not base.exists():
                continue
            for path in base.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                out.append(path)
        result = tuple(out)
        self._files_cache[key] = result
        return result

    # ── source text ──────────────────────────────────────────────────────

    def source_for(self, path: Path) -> str | None:
        """UTF-8 source text of ``path``, cached. ``None`` on a read error
        (mirrors the tolerant behaviour every detector already has)."""
        if path in self._source_cache:
            return self._source_cache[path]
        try:
            text: str | None = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = None
        self._source_cache[path] = text
        return text

    # ── AST ──────────────────────────────────────────────────────────────

    def tree_for(self, path: Path) -> ast.AST | None:
        """Parsed AST of ``path``, cached, or ``None`` on read / syntax error.

        Convenience accessor for checks that want to ask the context directly.
        The runner-wide win comes from :meth:`install` patching ``ast.parse``
        so even checks that never reference the context are de-duplicated.
        """
        source = self.source_for(path)
        if source is None:
            return None
        try:
            return self.parse(source, filename=str(path))
        except SyntaxError:
            return None

    def parse(self, source: str, *, filename: str = "<unknown>") -> ast.AST:
        """Memoised ``ast.parse`` — the cache the installed wrapper delegates
        to. Keyed by ``(filename, source-text)``; a miss parses with the real
        stdlib parser and records the tree."""
        by_text = self._tree_cache.get(filename)
        if by_text is not None:
            hit = by_text.get(source)
            if hit is not None:
                self.parse_hits += 1
                return hit
        else:
            by_text = {}
            self._tree_cache[filename] = by_text
        tree = _REAL_AST_PARSE(source, filename=filename)
        by_text[source] = tree
        self.parse_misses += 1
        return tree

    def walk(self, node: ast.AST) -> list[ast.AST]:
        """Memoised ``ast.walk`` — return the full BFS node list for ``node``.

        The dominant cost of the fitness suite is re-walking the same parsed
        trees: ~25 ``FitnessRule`` subclasses plus the standalone AST checks
        each ``ast.walk`` every in-scope file's tree independently, so a
        15k-node tree gets traversed dozens of times. Because the parse cache
        returns the SAME tree object for a given file, the walk can be done
        ONCE and the materialised node list shared by object identity.

        Order is byte-for-byte the stdlib ``ast.walk`` BFS order, so every
        caller — including the ones that build parent maps from walk order —
        sees an identical sequence. Returns a list (not a generator); the
        installed wrapper yields from it, preserving the generator contract.
        """
        key = id(node)
        cached = self._walk_cache.get(key)
        if cached is not None:
            self.walk_hits += 1
            return cached
        nodes = list(_REAL_AST_WALK(node))
        self._walk_cache[key] = nodes
        self.walk_misses += 1
        return nodes

    @property
    def distinct_files_parsed(self) -> int:
        """How many distinct ``(filename, source)`` pairs were parsed — the
        ceiling the parse-once invariant holds against."""
        return self.parse_misses

    # ── installation ─────────────────────────────────────────────────────

    @contextmanager
    def install(self) -> Iterator[CheckContext]:
        """Patch ``ast.parse`` and ``ast.walk`` to route through this context's
        caches for the duration of the ``with`` block, restoring the real
        functions on exit.

        ``ast.parse``: only the plain shapes the fitness checks use are
        memoised — ``ast.parse(source)`` and ``ast.parse(source, filename=...)``.
        Any call passing a non-default ``mode`` / ``type_comments`` /
        ``feature_version`` (no fitness check does) falls straight through to
        the real parser uncached, so the wrapper can never change semantics for
        an exotic caller.

        ``ast.walk``: yields from the memoised BFS node list (keyed by tree
        identity), so re-walking a cached tree is a list iteration instead of a
        fresh traversal. The yield order is the stdlib BFS order, identical for
        every caller.
        """

        # Signatures kept loose (``Any``) so the wrappers are drop-in
        # replacements for ``ast.parse`` / ``ast.walk``'s own overloaded shapes.
        def _cached_parse(
            source: Any,
            filename: Any = "<unknown>",
            mode: Any = "exec",
            *args: Any,
            **kwargs: Any,
        ) -> ast.AST:
            # Only the plain ``source`` / ``filename`` shape is memoisable;
            # anything exotic delegates straight to the real parser.
            if mode != "exec" or args or kwargs or not isinstance(source, str):
                exotic: ast.AST = _REAL_AST_PARSE(source, filename, mode, *args, **kwargs)
                return exotic
            return self.parse(source, filename=filename)

        def _cached_walk(node: ast.AST) -> Iterator[ast.AST]:
            yield from self.walk(node)

        prev_parse = ast.parse
        prev_walk = ast.walk
        # ``ast.parse`` is an overloaded stub; assigning a plain callable to it
        # is a deliberate run-scoped memoisation seam (restored in ``finally``),
        # which mypy can't express against the overload set.
        ast.parse = _cached_parse  # type: ignore[assignment]  # run-scoped parse-cache seam; restored in finally
        ast.walk = _cached_walk
        try:
            yield self
        finally:
            # Restore the pristine overloaded stdlib functions captured above.
            ast.parse = prev_parse
            ast.walk = prev_walk
