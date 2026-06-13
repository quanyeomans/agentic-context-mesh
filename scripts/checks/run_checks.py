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

* ``script`` set (e.g. ``"check-f44-engagement-firm-boundary.sh"``) →
  run that script. ``.sh`` → ``bash scripts/checks/<script>``; ``.py``
  → ``python3 scripts/checks/<script>``.
* ``script`` unset → the default python check
  ``python3 scripts/checks/check_<check>.py``.

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
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
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
    catalogue order. Aggregate ledger; return 0 iff all passed."""
    seen_scripts: set[str] = set()
    failures: list[str] = []
    ran = 0
    skipped = 0
    for entry in entries:
        script = resolve_script(entry)
        if script in seen_scripts:
            continue
        seen_scripts.add(script)
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
