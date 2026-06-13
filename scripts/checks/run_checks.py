#!/usr/bin/env python3
"""Catalogue-driven fitness-function runner — the single dispatch surface.

Motivation (EPIC #499 Phase 2)
------------------------------
Before this runner, registering a fitness rule meant editing five files
in lockstep: ``_rule_catalogue.py`` (metadata), ``run-all.sh`` (explicit
per-check invocations), ``.pre-commit-config.yaml`` (per-rule hooks),
``docs/architecture/fitness-functions.md`` (prose), and ``CLAUDE.md``
(the grouped section). Six parallel rule agents editing those five files
produced six cherry-pick conflicts. This runner makes
``scripts/checks/_rule_catalogue.py`` the single source of truth: it
reads the catalogue and DERIVES the invocation set, so ``run-all.sh``,
pre-commit, and the docs all consume the catalogue.

Dispatch convention
-------------------
Each :class:`~_rule_catalogue.RuleEntry` resolves to exactly one check
script:

* ``script`` unset → the default python check
  ``check_<check>.py``. These run IN-PROCESS (#499 Phase 2 stage 4a):
  the runner imports the module and calls its ``main() -> int`` inside a
  single process, sharing one :class:`~_check_context.CheckContext` whose
  AST cache parses every file at most once. The per-rule verdict is the
  check's own ``main()`` return code; a check that raises is isolated into
  a FAIL, never aborting the ledger.
* ``script`` set to a ``check-*.sh`` (e.g.
  ``"check-no-internal-patches.sh"``) → run that REAL shell detector as a
  guarded subprocess. Only the handful of rules whose verdict is produced
  by bash (the ``grep`` + ``arch_gate`` detectors F1/F2/F3/F4/F10) carry a
  ``script`` override; the gating logic lives in the shell, not in a python
  ``main()``, so they cannot run in-process.
* The F7/F9 coverage check (``check_per_file_coverage.py``) runs as a
  subprocess too — it takes a runtime Cobertura-XML argument and
  ``sys.exit``s on a malformed report.

Entries with ``status="proposed"`` / ``check="(proposed)"`` have no
script yet and are skipped. Entries with ``run_all=False`` run elsewhere
in the SDLC (release-time, security stage, out-of-band) and are excluded
from ``--all`` — exactly mirroring what ``run-all.sh`` dispatched before
this change. Distinct entries that resolve to the SAME script (e.g. F7
and the conditional coverage path) dispatch that script once.

Modes
-----
* ``--all`` — every in-scope rule (the full tree). The entrypoint
  ``run-all.sh`` calls; safe-commit / CI Stage 0 consume it.
* ``--staged`` — only rules whose ``scope`` plausibly intersects the
  staged paths (``git diff --cached --name-only``). When scope can't be
  resolved to a path predicate, the rule runs (fail-safe: never silently
  drop a rule). pre-commit calls this.
* ``--gate <id>`` — one rule by catalogue id (e.g. ``F26``).

Output contract (F83 gate-runner discipline)
--------------------------------------------
The runner prints a named ``run`` line and a ``PASS`` / ``FAIL`` verdict
line PER RULE, then a final aggregate verdict. Every subprocess is
guarded — a check that raises or exits non-zero is recorded as a FAIL
for its rule, never aborting the ledger. Exit code is non-zero iff any
dispatched rule failed.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import inspect
import io
import os
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _check_context import CheckContext
from _rule_catalogue import ALL_ENTRIES, RuleEntry

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECKS_DIR = REPO_ROOT / "scripts" / "checks"

# The coverage check (F7 / F9) is the one rule taking a runtime argument
# (the Cobertura XML path) and the one rule run-all.sh dispatches
# conditionally — it needs a coverage report that only exists after a
# coverage run. Callers (safe-commit.sh) pass the path via
# KAIRIX_COVERAGE_XML; a standalone run defaults to coverage.xml. When
# neither exists, the rule is skipped — exactly as run-all.sh did.
_COVERAGE_CHECK = "check_per_file_coverage.py"

_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[0;33m"
_RESET = "\033[0m"


def resolve_script(entry: RuleEntry) -> str:
    """Return the check-script filename (under ``scripts/checks/``) for
    ``entry`` — the ``script`` override, or the default
    ``check_<check>.py``.
    """
    if entry.script:
        return entry.script
    return f"check_{entry.check}.py"


def is_dispatchable(entry: RuleEntry) -> bool:
    """True iff ``entry`` has a real check to run (not a ``proposed``
    placeholder)."""
    return entry.status != "proposed" and entry.check != "(proposed)"


# ── scope → staged-path predicate (the --staged narrowing) ──────────────
#
# A rule's ``scope`` is a coarse shape, not a path glob, so the staged
# narrowing is deliberately permissive: it maps a scope to the path
# prefixes the rule could plausibly fire on. Anything not confidently
# narrowable falls through to "run it" — the whole point is to never
# silently skip a rule that a staged file might violate.
_CROSS_CUTTING_SCOPES = frozenset({"cross-cutting", "per-commit", "per-flag", "per-table", "per-protocol-method"})


def _rule_touches_staged(entry: RuleEntry, staged: list[str]) -> bool:
    """Decide whether ``entry`` should run given the ``staged`` paths.

    Conservative: returns ``True`` (run the rule) whenever the staged
    set could plausibly contain a file the rule governs. Only returns
    ``False`` when the rule is confidently a no-op for the staged set.
    """
    if not staged:
        # pre-commit with no staged files (e.g. --all-files dispatch
        # quirk) — run everything rather than silently pass.
        return True
    # Cross-cutting / whole-tree rules always run: their detectors walk
    # the repo, not a per-file delta, so a staged change anywhere can
    # flip them.
    if entry.scope in _CROSS_CUTTING_SCOPES:
        return True
    # Shell-script rules (F83 etc.) fire on staged .sh changes; the
    # F3 suppression-rationale rule fires on any .py. Resolve by the
    # script's own breadth — cheapest correct answer is "run it".
    script = resolve_script(entry)
    if script.endswith(".sh"):
        # gate-runner / shell-scoped rules: run if any shell or python
        # source is staged (their detectors walk fixed trees).
        return any(p.endswith((".sh", ".py", ".feature", ".yml", ".yaml")) for p in staged)
    # Default python checks walk kairix/ and/or tests/ — run when any
    # python, feature, or config source is staged.
    return any(p.endswith((".py", ".feature", ".yml", ".yaml", ".properties", ".toml")) for p in staged)


def _staged_paths() -> list[str]:
    """``git diff --cached --name-only`` — staged file paths, repo-relative.

    Guarded: a git failure returns an empty list, which the caller
    treats as "run everything" (fail-safe)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ── dispatch ────────────────────────────────────────────────────────────


def _coverage_xml_path() -> Path | None:
    """The Cobertura XML the coverage check should read, or ``None`` to
    skip. ``KAIRIX_COVERAGE_XML`` wins (safe-commit's per-invocation
    artifact); else the repo-root ``coverage.xml``; skip if neither
    exists."""
    env_path = os.environ.get("KAIRIX_COVERAGE_XML")
    candidate = Path(env_path) if env_path else (REPO_ROOT / "coverage.xml")
    return candidate if candidate.exists() else None


# ── in-process vs subprocess dispatch policy ────────────────────────────
#
# Stage 4a (#499 Phase 2): most rules are pure-python AST/text checks with a
# zero-arg ``main() -> int`` that prints its own ``ok``/``FAIL`` gate line and
# returns the exit code. Those run IN-PROCESS, sharing one ``CheckContext`` so
# the AST cache parses every file at most once. Two classes stay as guarded
# subprocesses because they are genuinely NOT pure python:
#
#   * ``check-*.sh`` real shell detectors — F1/F2 (python emits paths, the
#     shell ``arch_gate`` does the baseline diff + verdict), F3/F4/F10 (grep
#     + arch_gate), and any other shell-scoped gate. Their verdict is produced
#     by bash, not by the python ``main()``.
#   * the F7/F9 coverage check — it takes a runtime Cobertura-XML argument and
#     ``sys.exit``s on a malformed report; it is also conditionally skipped.
#
# A rule dispatches in-process iff its resolved script is ``check_<x>.py``
# (not a ``.sh``) AND is not the coverage check.
_SHELL_SUFFIX = ".sh"


def _dispatches_in_process(entry: RuleEntry) -> bool:
    """True iff ``entry``'s check runs in-process (pure-python, no runtime arg)."""
    script = resolve_script(entry)
    if script.endswith(_SHELL_SUFFIX):
        return False
    return script != _COVERAGE_CHECK


def _load_check_main(script: str) -> Callable[[], int]:
    """Import the check module for ``script`` (a ``check_<x>.py`` filename) and
    return a zero-arg callable that invokes its ``main``.

    Some checks declare ``main(argv: list[str] | None = None)`` and default to
    ``argparse``'s ``parse_args(None)`` — which reads ``sys.argv``. Under the
    subprocess runner that was the per-check process's empty argv (the static
    half / no runtime arg); in-process, ``sys.argv`` is the RUNNER's
    ``--all`` / ``--skip-coverage``, which the check's parser would reject. So
    when ``main`` accepts an ``argv`` parameter we pass an explicit empty list
    — reproducing exactly the no-arguments subprocess invocation. Imported once
    per module; ``importlib`` caches it in ``sys.modules``."""
    module_name = script[: -len(".py")]
    module = importlib.import_module(module_name)
    main_fn = module.main
    accepts_argv = bool(inspect.signature(main_fn).parameters)

    def _invoke() -> int:
        result = main_fn([]) if accepts_argv else main_fn()
        return int(result)

    return _invoke


def _run_one_inprocess(entry: RuleEntry, ctx: CheckContext) -> int:
    """Dispatch ``entry``'s pure-python check IN-PROCESS, sharing ``ctx``.

    Prints the identical ``run`` / ``PASS`` / ``FAIL`` framing the subprocess
    path prints, with the check's own stdout/stderr replayed inline between
    them (so the ledger interleaves exactly as the unbuffered subprocess
    runner did). The check is fully isolated:

      * its ``main()`` is called inside a try/except over ``BaseException``;
        a raised exception OR a ``SystemExit`` is converted to a FAIL with the
        traceback, exactly as a non-zero subprocess exit would have been — one
        crashing check never aborts the ledger;
      * a non-int / non-zero return is treated as a failure;
      * stdout/stderr are captured and replayed so a check that forgets to
        flush, or writes to stderr, lands in the same stream the developer saw.
    """
    script = resolve_script(entry)
    print(f"{_YELLOW}run [{entry.id}]{_RESET} {script}")

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    crashed = False
    rc = 1
    try:
        check_main = _load_check_main(script)
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            result = check_main()
        rc = result if isinstance(result, int) else 1
    except BaseException:
        # Isolation boundary: one check must never abort the ledger. Every
        # failure mode — a raised exception, a SystemExit, a KeyboardInterrupt
        # bubbling out of a check's main() — is converted to a FAIL verdict,
        # exactly as a non-zero subprocess exit would have been.
        crashed = True
        traceback.print_exc(file=err_buf)
        rc = 1

    # Replay the check's captured output inline, preserving stream separation
    # so the merged ledger is byte-identical to the unbuffered subprocess form.
    captured_out = out_buf.getvalue()
    if captured_out:
        sys.stdout.write(captured_out)
    captured_err = err_buf.getvalue()
    if captured_err:
        sys.stderr.write(captured_err)

    if rc == 0:
        print(f"{_GREEN}PASS [{entry.id}]{_RESET} {entry.summary[:88]}")
        return 0
    suffix = "" if crashed else f" (exit {rc})"
    print(f"{_RED}FAIL [{entry.id}]{_RESET} {entry.summary[:88]}{suffix}")
    if entry.exemplar:
        print(f"  paved-road: python3 scripts/checks/rules.py --rule {entry.id}")
    return 1


def _run_one(entry: RuleEntry, *, skip_coverage: bool) -> int | None:
    """Dispatch ``entry``'s check as a guarded subprocess. Print a named
    ``run`` line and a ``PASS`` / ``FAIL`` verdict. Return 0 on pass,
    1 on fail (including a missing script or a crashing check), or
    ``None`` when the rule was intentionally skipped (coverage report
    absent) — mirroring run-all.sh's conditional coverage stage."""
    script = resolve_script(entry)
    script_path = CHECKS_DIR / script
    interpreter = "bash" if script.endswith(".sh") else sys.executable

    extra_args: list[str] = []
    if script == _COVERAGE_CHECK:
        if skip_coverage:
            print(f"{_YELLOW}skip [{entry.id}]{_RESET} {script} — --skip-coverage")
            return None
        coverage_xml = _coverage_xml_path()
        if coverage_xml is None:
            print(f"{_YELLOW}skip [{entry.id}]{_RESET} {script} — coverage report not found")
            print("   run: pytest --cov=kairix --cov-report=xml first, then re-run this check.")
            return None
        extra_args = [str(coverage_xml)]

    print(f"{_YELLOW}run [{entry.id}]{_RESET} {script}")

    if not script_path.exists():
        print(f"{_RED}FAIL [{entry.id}]{_RESET} — check script not found: scripts/checks/{script}")
        print("   fix: restore the script or correct the catalogue entry's check/script field.")
        return 1

    try:
        result = subprocess.run(
            [interpreter, str(script_path), *extra_args],
            cwd=REPO_ROOT,
            check=False,
        )
        rc = result.returncode
    except OSError as exc:
        print(f"{_RED}FAIL [{entry.id}]{_RESET} — could not launch {script}: {exc}")
        return 1

    if rc == 0:
        print(f"{_GREEN}PASS [{entry.id}]{_RESET} {entry.summary[:88]}")
        return 0
    print(f"{_RED}FAIL [{entry.id}]{_RESET} {entry.summary[:88]} (exit {rc})")
    # Paved-road footer (#499 Phase 2): when a FAILING rule carries a
    # curated exemplar, point the agent straight at the query surface
    # that surfaces it. Only the existing FAIL verdict line is the F83
    # named verdict; this is an ADDED affordance line, not a replacement.
    if entry.exemplar:
        print(f"  paved-road: python3 scripts/checks/rules.py --rule {entry.id}")
    return 1


def _select_all() -> list[RuleEntry]:
    """In-scope rules for ``--all``: dispatchable AND ``run_all`` — the
    set ``run-all.sh`` dispatched before the catalogue-driven cutover."""
    return [e for e in ALL_ENTRIES if is_dispatchable(e) and e.run_all]


def _select_staged() -> list[RuleEntry]:
    """``--all`` set, narrowed to rules a staged change could trip."""
    staged = _staged_paths()
    return [e for e in _select_all() if _rule_touches_staged(e, staged)]


def _select_gate(gate_id: str) -> list[RuleEntry]:
    """Rules whose catalogue ``id`` matches ``gate_id`` (case-insensitive)."""
    return [e for e in ALL_ENTRIES if e.id.lower() == gate_id.lower() and is_dispatchable(e)]


def _dispatch(entries: list[RuleEntry], *, skip_coverage: bool) -> int:
    """Run every entry once per distinct resolved script (dedup), in
    catalogue order. Aggregate ledger; return 0 iff all passed.

    Pure-python rules run IN-PROCESS sharing one :class:`CheckContext` (so the
    AST cache parses every file at most once); shell detectors and the coverage
    check run as guarded subprocesses. The dispatch ORDER, the per-rule ``run``
    / ``PASS`` / ``FAIL`` lines, and the aggregate verdict are identical to the
    subprocess-per-rule runner — only the SOURCE of each verdict changes.
    """
    seen_scripts: set[str] = set()
    failures: list[str] = []
    ran = 0
    skipped = 0
    ctx = CheckContext(repo_root=REPO_ROOT)
    with ctx.install():
        for entry in entries:
            script = resolve_script(entry)
            if script in seen_scripts:
                continue
            seen_scripts.add(script)
            if _dispatches_in_process(entry):
                result: int | None = _run_one_inprocess(entry, ctx)
            else:
                result = _run_one(entry, skip_coverage=skip_coverage)
            if result is None:
                skipped += 1
                continue
            ran += 1
            if result != 0:
                failures.append(entry.id)

    print()
    if failures:
        print(
            f"{_RED}=== Architecture fitness functions FAILED ==={_RESET} "
            f"({len(failures)}/{ran} rule(s) failed: {', '.join(failures)})"
        )
        return 1
    print(f"{_GREEN}=== All {ran} architecture fitness functions passed ==={_RESET}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse mode flags and dispatch. ``--all`` is the default."""
    parser = argparse.ArgumentParser(
        prog="run_checks.py",
        description="Catalogue-driven fitness-function runner (#499 Phase 2).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="run every in-scope rule (default)")
    group.add_argument("--staged", action="store_true", help="run only rules a staged change could trip")
    group.add_argument("--gate", metavar="ID", help="run one rule by catalogue id (e.g. F26)")
    parser.add_argument(
        "--skip-coverage",
        action="store_true",
        help="skip the F7/F9 per-file coverage check (needs a coverage.xml report)",
    )
    args = parser.parse_args(argv)

    if args.gate:
        entries = _select_gate(args.gate)
        if not entries:
            print(f"{_RED}no catalogue rule with id {args.gate!r}{_RESET}")
            print("   fix: pass a real id (see scripts/checks/_rule_catalogue.py) or run --all.")
            return 2
        print(f"=== Architecture fitness function: {args.gate} ===")
    elif args.staged:
        entries = _select_staged()
        print("=== Architecture fitness functions (staged) ===")
    else:
        entries = _select_all()
        print("=== Architecture fitness functions ===")

    return _dispatch(entries, skip_coverage=args.skip_coverage)


if __name__ == "__main__":
    sys.exit(main())
