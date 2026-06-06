"""
Wikilink injection CLI for kairix.

Usage:
  kairix wikilinks inject --changed            inject files modified since last run
  kairix wikilinks inject --path path/to.md    inject a single file
  kairix wikilinks inject --dry-run            show what would be injected, no writes
  kairix wikilinks audit                       broken links + unlinked mentions report
  kairix wikilinks status                      entity count, last run, files processed
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.knowledge.wikilinks.injector import (
    _LOG_PATH,
    MAX_FILE_SIZE,
    inject_file,
    should_inject,
)
from kairix.knowledge.wikilinks.resolver import get_entities
from kairix.paths import KairixPaths, wikilinks_last_run_path

# Timestamp file to track last run — env read lives in kairix.paths (F4).
_LAST_RUN_PATH = str(wikilinks_last_run_path())


# ---------------------------------------------------------------------------
# Deps — DI seam for every helper module-level function this CLI consumes.
# ---------------------------------------------------------------------------


def _default_weekly_report(root: str, entities: list[Any], *, paths: KairixPaths) -> str:
    """Production default for ``WikilinksCliDeps.weekly_report``."""
    from kairix.knowledge.wikilinks.audit import weekly_report

    return weekly_report(root, entities, paths=paths)


def _default_marker_paths() -> tuple[str, str]:
    """Production default for ``WikilinksCliDeps.marker_paths``.

    Returns ``(_LAST_RUN_PATH, _LOG_PATH)`` from the live module
    globals; tests pass a callable returning ``(tmp_path/"last_run",
    tmp_path/"log.jsonl")`` to avoid touching real cache state.
    """
    return _LAST_RUN_PATH, _LOG_PATH


@dataclass(frozen=True)
class WikilinksCliDeps:
    """Injectable dependencies for the wikilinks CLI handlers.

    Mirrors ``EntityValidateDeps`` shape — every callable field is
    non-Optional with a ``default_factory`` wiring the production
    helper. Tests pass ``WikilinksCliDeps(get_entities=fake, ...)`` to
    avoid touching Neo4j / disk; production callers leave ``deps=None``
    and the defaults reproduce the historical module-global behaviour.

    ``marker_paths`` is the canonical seam for ``_LAST_RUN_PATH`` /
    ``_LOG_PATH``: tests return ``(tmp_path / "lr", tmp_path / "log")``
    and the marker / log read+write helpers consult the provider
    instead of the module globals.
    """

    get_entities: Callable[[], list[Any]] = field(default_factory=lambda: get_entities)
    should_inject: Callable[..., bool] = field(default_factory=lambda: should_inject)
    inject_file: Callable[..., list[str]] = field(default_factory=lambda: inject_file)
    weekly_report: Callable[..., str] = field(default_factory=lambda: _default_weekly_report)
    marker_paths: Callable[[], tuple[str, str]] = field(default_factory=lambda: _default_marker_paths)


def _extract_document_root_flag(argv: list[str]) -> tuple[list[str], str | None]:
    """Pop ``--document-root PATH`` out of ``argv`` ahead of subcommand dispatch.

    The wikilinks CLI uses hand-rolled argv parsing per subcommand
    (each ``_*_cmd`` consumes its own flags), so we strip the global
    ``--document-root`` flag here before the subcommand sees it. Returns
    ``(argv_without_flag, value_or_None)``.
    """
    if "--document-root" not in argv:
        return argv, None
    idx = argv.index("--document-root")
    if idx + 1 >= len(argv):
        print("--document-root requires a path argument", file=sys.stderr)
        sys.exit(1)
    value = argv[idx + 1]
    return argv[:idx] + argv[idx + 2 :], value


def _replace_document_root(paths: KairixPaths, document_root: Path) -> KairixPaths:
    """Return a copy of ``paths`` with ``document_root`` replaced.

    F30 subprocess seam — keeps the existing in-process ``paths=`` kwarg
    winning, and only kicks in when no kwarg was supplied (the
    subprocess test case). Explicit construction (rather than
    ``dataclasses.replace``) keeps Sonar's type analysis able to see
    the concrete return type.
    """
    return KairixPaths(
        document_root=document_root,
        db_path=paths.db_path,
        log_dir=paths.log_dir,
        workspace_root=paths.workspace_root,
    )


def main(
    argv: list[str] | None = None,
    *,
    paths: KairixPaths | None = None,
    deps: WikilinksCliDeps | None = None,
) -> None:
    """Entry point for `kairix wikilinks` subcommand.

    Constructs the runtime ``KairixPaths`` once at the boundary and passes
    it down to every command handler — the only place this CLI module
    calls ``KairixPaths.resolve()``. Subcommands receive ``paths`` as a
    parameter, so tests inject a ``FakePaths`` via the ``paths`` keyword
    instead of monkeypatching ``KAIRIX_*`` environment variables.

    ``deps`` is the canonical DI seam for module-level helper functions
    (entity loader, injector, audit report, marker / log paths). Tests
    pass a ``WikilinksCliDeps(...)`` constructed with fakes; production
    leaves ``deps=None`` and the dataclass's ``default_factory`` wires
    the production helpers.

    ``--document-root PATH`` is the F30 subprocess seam: when supplied
    (and no explicit ``paths`` kwarg was injected) it overrides the
    document_root component of the resolved ``KairixPaths``. Matches the
    canonical pattern in ``kairix store crawl --document-root``.
    """
    if argv is None:
        argv = sys.argv[2:]  # strip "kairix wikilinks"

    if not argv:
        print(__doc__)
        sys.exit(0)

    argv, document_root_override_arg = _extract_document_root_flag(list(argv))

    if paths is None:
        paths = KairixPaths.resolve()
        if document_root_override_arg is not None:
            paths = _replace_document_root(paths, Path(document_root_override_arg))

    if deps is None:
        deps = WikilinksCliDeps()

    if not argv:
        print(__doc__)
        sys.exit(0)

    subcmd = argv[0]

    if subcmd in ("--help", "-h", "help"):
        print(__doc__)
        sys.exit(0)
    elif subcmd == "inject":
        _inject_cmd(argv[1:], paths=paths, deps=deps)
    elif subcmd == "audit":
        _audit_cmd(argv[1:], paths=paths, deps=deps)
    elif subcmd == "status":
        _status_cmd(argv[1:], deps=deps)
    else:
        print(f"Unknown wikilinks subcommand: {subcmd}\n{__doc__}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# inject
# ---------------------------------------------------------------------------


def _inject_cmd(argv: list[str], *, paths: KairixPaths, deps: WikilinksCliDeps) -> None:
    """Handle `kairix wikilinks inject` with flags."""
    dry_run = "--dry-run" in argv
    changed_only = "--changed" in argv
    single_path: str | None = None

    if "--path" in argv:
        idx = argv.index("--path")
        if idx + 1 >= len(argv):
            print("--path requires a file path argument", file=sys.stderr)
            sys.exit(1)
        single_path = argv[idx + 1]

    entities = deps.get_entities()
    if not entities:
        print(
            "⚠️  No entities loaded — check Neo4j connection and bootstrap index.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Loaded {len(entities)} entities.")
    if dry_run:
        print("Dry-run mode: no files will be modified.\n")

    if single_path:
        _inject_single(single_path, entities, dry_run, paths=paths, deps=deps)
    elif changed_only:
        _inject_changed(entities, dry_run, paths=paths, deps=deps)
    else:
        _inject_all(entities, dry_run, paths=paths, deps=deps)

    if not dry_run:
        write_last_run_marker_at(deps.marker_paths()[0])


def _inject_single(
    path: str,
    entities: list[Any],
    dry_run: bool,
    *,
    paths: KairixPaths,
    deps: WikilinksCliDeps,
) -> None:
    """Inject wikilinks into a single file."""
    if not deps.should_inject(path, paths=paths):
        print(f"⚠️  {path} is not eligible for injection.")
        return
    injected = deps.inject_file(path, entities, dry_run=dry_run, paths=paths)
    if injected:
        mode = "(dry-run)" if dry_run else ""
        print(f"  ✅ {path} {mode}")
        for name in injected:
            print(f"     + [[{name}]]")
    else:
        print(f"  — {path}: no new links")


def _inject_files(
    files: list[str],
    entities: list[Any],
    dry_run: bool,
    *,
    paths: KairixPaths,
    deps: WikilinksCliDeps,
) -> None:
    """Run inject_file across ``files`` and print a totals line. Shared by all/changed."""
    total_files = 0
    total_links = 0
    for path in files:
        injected = deps.inject_file(path, entities, dry_run=dry_run, paths=paths)
        if not injected:
            continue
        total_files += 1
        total_links += len(injected)
        mode = "(dry-run)" if dry_run else ""
        print(f"  ✅ {path} {mode}")
        for name in injected:
            print(f"     + [[{name}]]")
    print(f"\nDone. {total_files} files updated, {total_links} wikilinks injected.")


def _inject_all(
    entities: list[Any],
    dry_run: bool,
    *,
    paths: KairixPaths,
    deps: WikilinksCliDeps,
) -> None:
    """Inject wikilinks into all eligible vault and workspace files."""
    _inject_files(gather_eligible_files_with_deps(paths, deps), entities, dry_run, paths=paths, deps=deps)


def _filter_changed_since(files: list[str], cutoff: float) -> list[str]:
    """Return paths whose mtime is at or after ``cutoff``; missing-stat paths skipped."""
    changed: list[str] = []
    for path in files:
        try:
            if Path(path).stat().st_mtime >= cutoff:
                changed.append(path)
        except OSError:
            continue
    return changed


def _inject_changed(
    entities: list[Any],
    dry_run: bool,
    *,
    paths: KairixPaths,
    deps: WikilinksCliDeps,
) -> None:
    """Inject only files modified since last run."""
    last_run = read_last_run_marker_at(deps.marker_paths()[0])
    if last_run is None:
        print("No previous run found — processing all eligible files.")
        _inject_all(entities, dry_run, paths=paths, deps=deps)
        return

    changed = _filter_changed_since(gather_eligible_files_with_deps(paths, deps), last_run)
    if not changed:
        print(f"No files modified since last run ({fmt_ts(last_run)}). Nothing to do.")
        return

    print(f"Processing {len(changed)} files modified since {fmt_ts(last_run)}.\n")
    _inject_files(changed, entities, dry_run, paths=paths, deps=deps)


def _eligible_md_files_under(root: str, paths: KairixPaths, deps: WikilinksCliDeps | None = None) -> Iterator[str]:
    """Yield eligible .md files under ``root`` that fit within MAX_FILE_SIZE."""
    eligibility = (deps or WikilinksCliDeps()).should_inject
    p = Path(root)
    if not p.exists():
        return
    for md_file in p.rglob("*.md"):
        path_str = str(md_file)
        if not eligibility(path_str, paths=paths):
            continue
        try:
            if md_file.stat().st_size <= MAX_FILE_SIZE:
                yield path_str
        except OSError:
            continue


def gather_eligible_files(paths: KairixPaths) -> list[str]:
    """Collect all eligible .md files from vault and workspaces.

    Kept as the public test surface — defers to the deps-aware helper
    with a default ``WikilinksCliDeps``.
    """
    return gather_eligible_files_with_deps(paths, WikilinksCliDeps())


def gather_eligible_files_with_deps(paths: KairixPaths, deps: WikilinksCliDeps) -> list[str]:
    """Collect eligible .md files from vault + workspaces, using ``deps``."""
    result: list[str] = []
    for root in (str(paths.document_root), str(paths.workspace_root)):
        result.extend(_eligible_md_files_under(root, paths, deps))
    return result


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _audit_cmd(_argv: list[str], *, paths: KairixPaths, deps: WikilinksCliDeps) -> None:
    """Handle `kairix wikilinks audit`.

    The ``_argv`` parameter is accepted for sub-handler signature uniformity
    with the inject/dry-run handlers (F19: underscore-prefixed); the audit
    subcommand has no per-invocation options beyond the shared ``paths``
    context.
    """
    entities = deps.get_entities()
    report = deps.weekly_report(str(paths.document_root), entities, paths=paths)
    print(report)

    # Optionally save report to vault
    report_path = paths.document_root / "04-Agent-Knowledge" / "shared" / "wikilink-audit-report.md"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        print(f"\n📄 Report saved to {report_path}")
    except OSError as e:
        print(f"\n⚠️  Could not save report: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _status_cmd(_argv: list[str], *, deps: WikilinksCliDeps) -> None:
    """Handle `kairix wikilinks status`.

    ``_argv`` is accepted for sub-handler signature uniformity with the
    inject/audit handlers; status takes no per-invocation options.
    """
    last_run_path, log_path = deps.marker_paths()
    entities = deps.get_entities()
    last_run = read_last_run_marker_at(last_run_path)
    log_entries = read_log_entries_at(log_path)

    print("🔗 kairix Wikilinks Status")
    print("─" * 40)
    print(f"Entities loaded:    {len(entities)}")
    print(f"Last run:           {fmt_ts(last_run) if last_run else 'never'}")

    if log_entries:
        total_files = len(log_entries)
        total_links = sum(len(e.get("injected", [])) for e in log_entries)
        real = sum(1 for e in log_entries if not e.get("dry_run"))
        dry = sum(1 for e in log_entries if e.get("dry_run"))
        print(f"Total log entries:  {total_files}")
        print(f"  Real injections:  {real}")
        print(f"  Dry runs:         {dry}")
        print(f"  Total links added: {total_links}")
    else:
        print("Injection log:      empty")

    print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_last_run_marker_at(path: str) -> None:
    """Write current timestamp to ``path``. Swallows OSError (best-effort)."""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def read_last_run_marker_at(path: str) -> float | None:
    """Read a unix timestamp from ``path``. None on missing/invalid."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        return float(text)
    except (OSError, ValueError):
        return None


def read_log_entries_at(path: str) -> list[dict[str, Any]]:
    """Read JSONL entries from ``path``. Bad / blank lines are skipped."""
    entries: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return entries


def write_last_run_marker() -> None:
    """Write current timestamp to last-run marker file.

    Public shim — delegates to ``write_last_run_marker_at`` against the
    module-level ``_LAST_RUN_PATH``. Tests prefer the deps-aware seam
    via ``WikilinksCliDeps(marker_paths=...)``.
    """
    write_last_run_marker_at(_LAST_RUN_PATH)


def read_last_run_marker() -> float | None:
    """Read timestamp from last-run marker file. Returns None if not found."""
    return read_last_run_marker_at(_LAST_RUN_PATH)


def read_log_entries() -> list[dict[str, Any]]:
    """Read all entries from injection log."""
    return read_log_entries_at(_LOG_PATH)


def fmt_ts(ts: float | None) -> str:
    """Format a Unix timestamp as a human-readable string."""
    if ts is None:
        return "never"
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
