"""Re-validate configured agent scopes against disk state (PR 1.5 / #420).

Sibling of :mod:`kairix.agents.onboarding.scanner` — where ``scanner``
*discovers* agent scopes by walking disk, ``doctor`` *validates*
already-configured scopes against disk and reports drift.

Per-surface validation rules:

* ``exists=False`` (dir missing or not a directory) → ``"path missing"``
  issue carrying the standard onboard remediation
* ``file_count=0`` (dir exists but no .md matching glob) → ``"no files
  matching glob"`` issue prompting glob / dir review
* ``most_recent_mtime`` older than 30 days → ``"stale — most recent
  file is N days old"`` issue
* Cross-agent overlap: when two surfaces' globs match the same file
  across different agents → ``"ambiguous: file <X> matches both
  <a1>.<s1> and <a2>.<s2>"`` issue on both surfaces

Per-agent overall:

* ``"ok"`` — every surface has files, all < 30 days, no overlap
* ``"warn"`` — staleness OR fallback-to-defaults OR no-files-matching
* ``"error"`` — any path missing OR no surfaces configured at all

The functions NEVER raise — disk-IO errors collapse to per-surface
``issues`` entries the operator can read and act on. Callers (the
``kairix doctor agent`` CLI + the ``tool_doctor_*`` MCP tools) depend
on this so a typo'd config path produces a clean error envelope rather
than a crash.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kairix.core.agents.scope import (
    AgentScope,
    get_agent_scope,
    load_agent_scopes,
)
from kairix.paths import WriteAccessProbe, probe_write_access, write_access_fix_hint

logger = logging.getLogger(__name__)

# F17 — operator-visible strings repeated across rules; one constant
# keeps the wording consistent + edit-in-one-place.
_ONBOARD_REMEDIATION = "run `kairix onboard agent --name {name}` to fix"
_FALLBACK_MESSAGE = "no explicit config — run `kairix onboard agent --name {name}` to commit explicit config"

# 30-day staleness threshold per the PR 1.5 spec. Lifted to a constant
# so the rule's intent is greppable and one edit covers tests + prod.
_STALE_THRESHOLD_DAYS = 30
_SECONDS_PER_DAY = 86_400

# Overall labels — exposed as constants so the CLI / MCP wrappers can
# compare against them without hardcoding string literals.
_OK = "ok"
_WARN = "warn"
_ERROR = "error"


@dataclass(frozen=True)
class SurfaceHealth:
    """Per-surface validation outcome.

    ``path`` and ``label`` echo the configured surface so operators
    can map issues back to the yaml block. ``exists``, ``file_count``,
    ``most_recent_mtime`` carry the disk-state probe. ``writable`` is the
    write-access probe (PLA-259) — False when the surface exists but kairix
    cannot create a file there (``:ro`` mount, wrong ownership), so the
    operator sees an unwritable memory surface BEFORE an agent tries to write
    to it. ``issues`` is the tuple of operator-facing strings — empty when
    the surface is healthy.
    """

    path: Path
    label: str
    exists: bool
    file_count: int
    most_recent_mtime: float | None
    issues: tuple[str, ...]
    writable: bool = True


@dataclass(frozen=True)
class AgentHealth:
    """Per-agent doctor outcome.

    ``name`` + ``harness`` echo the configured agent. ``surfaces``
    holds one :class:`SurfaceHealth` per configured surface; the
    tuple may be empty when the agent has no surfaces (which itself
    is an ``error`` overall). ``overall`` is the rolled-up label;
    ``issues`` carries agent-level messages (e.g. fallback-to-defaults).
    """

    name: str
    harness: str
    surfaces: tuple[SurfaceHealth, ...]
    overall: str
    issues: tuple[str, ...]


@dataclass(frozen=True)
class DoctorReport:
    """Bulk doctor outcome.

    ``agents`` carries one :class:`AgentHealth` per configured agent
    (sorted by name for stable rendering). ``overall`` rolls the per-
    agent labels: ``"error"`` wins over ``"warn"`` wins over ``"ok"``.
    ``summary_text`` is a single operator-friendly paragraph the CLI's
    default mode prints — keeps the human surface independent of the
    detailed per-agent rendering.
    """

    agents: tuple[AgentHealth, ...]
    overall: str
    summary_text: str


# ---------------------------------------------------------------------------
# Disk probe helpers — never raise
# ---------------------------------------------------------------------------


def _safe_iter_glob(directory: Path, glob: str) -> tuple[Path, ...]:
    """Return matching .md files for ``directory`` + ``glob``, or
    ``()`` on any disk IO error. Lets callers iterate without
    try/except boilerplate.
    """
    try:
        return tuple(p for p in directory.rglob(glob) if p.is_file())
    except OSError as exc:
        logger.debug("safe_iter_glob(%s, %r) failed: %s", directory, glob, exc)
        return ()


def _walk_surface_disk_state(
    path: Path,
    glob: str,
) -> tuple[bool, int, float | None]:
    """Return ``(exists, file_count, max_mtime)`` for one surface.

    Any disk IO error collapses to ``(False, 0, None)``. ``exists``
    is ``True`` only when the path is a directory.
    """
    try:
        exists = path.is_dir()
    except OSError as exc:
        logger.debug("path.is_dir(%s) failed: %s", path, exc)
        return False, 0, None
    if not exists:
        return False, 0, None
    files = _safe_iter_glob(path, glob)
    if not files:
        return True, 0, None
    latest: float | None = None
    count = 0
    for f in files:
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        count += 1
        if latest is None or mtime > latest:
            latest = mtime
    return True, count, latest


# ---------------------------------------------------------------------------
# Per-surface rules
# ---------------------------------------------------------------------------


def _write_access_issue(agent_name: str, probe: WriteAccessProbe) -> str:
    """Build the F21 issue for an existing-but-unwritable surface (PLA-259).

    Names WHICH path, WHICH permission (the errno + strerror), and HOW to fix
    (via :func:`kairix.paths.write_access_fix_hint`) so a ``:ro`` mount / wrong
    ownership surfaces as an actionable verdict, not a silent green.
    """
    detail = f"{probe.reason} [{probe.errno_name}]" if probe.errno_name else probe.reason
    return (
        f"not writable — {probe.path} ({detail}). "
        f"{write_access_fix_hint(probe.errno_name)}. "
        f"next: re-run `kairix doctor agent --name {agent_name}` after fixing the mount or ownership"
    )


def _classify_surface_issues(
    *,
    agent_name: str,
    exists: bool,
    file_count: int,
    most_recent_mtime: float | None,
    write_issue: str = "",
) -> tuple[str, ...]:
    """Apply the per-surface validation rules and return the issue tuple."""
    issues: list[str] = []
    if not exists:
        issues.append(
            f"path missing — {_ONBOARD_REMEDIATION.format(name=agent_name)}",
        )
        return tuple(issues)
    if write_issue:
        issues.append(write_issue)
    if file_count == 0:
        issues.append("no files matching glob — check glob pattern and dir contents")
        return tuple(issues)
    if most_recent_mtime is not None:
        age_days = int((time.time() - most_recent_mtime) / _SECONDS_PER_DAY)
        if age_days > _STALE_THRESHOLD_DAYS:
            issues.append(
                f"stale — most recent file is {age_days} days old",
            )
    return tuple(issues)


def _probe_surface(agent_name: str, path: Path, glob: str, label: str) -> SurfaceHealth:
    """Probe one surface and return its :class:`SurfaceHealth`.

    Beyond the disk-state walk (exists / file_count / mtime), probe whether
    kairix can actually write to an existing surface — a non-mutating
    (``create=False``) write-access probe so a ``:ro`` mount or wrong-owned
    directory is flagged before an agent's memory write fails (PLA-259).
    """
    exists, file_count, mtime = _walk_surface_disk_state(path, glob)
    # Write-access probe runs ONLY for an existing surface: a missing dir is
    # already an "path missing"/error via the disk-state walk, and probing a
    # missing path could otherwise create it. The dir exists here, so the
    # probe's default mkdir(exist_ok=True) is a no-op and only touches (then
    # removes) a probe file inside it (PLA-259).
    write_issue = ""
    writable = False
    if exists:
        probe = probe_write_access(path)
        writable = probe.writable
        if not probe.writable:
            write_issue = _write_access_issue(agent_name, probe)
    issues = _classify_surface_issues(
        agent_name=agent_name,
        exists=exists,
        file_count=file_count,
        most_recent_mtime=mtime,
        write_issue=write_issue,
    )
    return SurfaceHealth(
        path=path,
        label=label,
        exists=exists,
        file_count=file_count,
        most_recent_mtime=mtime,
        issues=issues,
        writable=writable,
    )


def _probe_agent_surfaces(scope: AgentScope) -> tuple[SurfaceHealth, ...]:
    """Probe every surface for one :class:`AgentScope`."""
    return tuple(_probe_surface(scope.name, s.path, s.glob, s.label) for s in scope.surfaces)


# ---------------------------------------------------------------------------
# Cross-agent ambiguity pass
# ---------------------------------------------------------------------------


def _flag_overlaps(
    healths: Iterable[tuple[str, SurfaceHealth, str]],
) -> dict[int, list[str]]:
    """Walk every pair of (agent, surface) and return a dict mapping
    surface id() → list of additional "ambiguous: …" issue strings.

    Two surfaces conflict when they exist on disk AND share at least
    one matching file path. The dict is keyed by id() because
    SurfaceHealth is frozen — callers rebuild the SurfaceHealth with
    the appended issues using the id() → extra-issues map.
    """
    overlaps: dict[int, list[str]] = {}
    items = list(healths)
    # Pre-resolve the matching file set per (agent, surface) so we
    # don't re-walk for every pairwise comparison.
    file_sets: list[tuple[str, SurfaceHealth, str, set[Path]]] = []
    for agent_name, sh, surface_label in items:
        if not sh.exists:
            file_sets.append((agent_name, sh, surface_label, set()))
            continue
        matched = set(_safe_iter_glob(sh.path, "**/*.md"))
        file_sets.append((agent_name, sh, surface_label, matched))

    for i, (a_name, a_sh, a_label, a_files) in enumerate(file_sets):
        for b_name, b_sh, b_label, b_files in file_sets[i + 1 :]:
            if a_name == b_name:
                continue
            shared = a_files & b_files
            if not shared:
                continue
            sample = next(iter(sorted(shared)))
            msg = f"ambiguous: file {sample} matches both {a_name}.{a_label} and {b_name}.{b_label}"
            overlaps.setdefault(id(a_sh), []).append(msg)
            overlaps.setdefault(id(b_sh), []).append(msg)
    return overlaps


def _apply_overlap_issues(
    healths: tuple[SurfaceHealth, ...],
    overlap_map: dict[int, list[str]],
) -> tuple[SurfaceHealth, ...]:
    """Return a new tuple of SurfaceHealth with overlap-issue strings
    appended to each surface's existing issues tuple.
    """
    out: list[SurfaceHealth] = []
    for sh in healths:
        extras = overlap_map.get(id(sh), [])
        if not extras:
            out.append(sh)
            continue
        merged = (*sh.issues, *extras)
        out.append(
            SurfaceHealth(
                path=sh.path,
                label=sh.label,
                exists=sh.exists,
                file_count=sh.file_count,
                most_recent_mtime=sh.most_recent_mtime,
                issues=merged,
                writable=sh.writable,
            ),
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Roll-up
# ---------------------------------------------------------------------------


def _rollup_overall(
    surfaces: tuple[SurfaceHealth, ...],
    agent_issues: tuple[str, ...],
) -> str:
    """Roll the surface-level + agent-level issue mix into one label."""
    if not surfaces:
        return _ERROR
    has_error = False
    has_warn = bool(agent_issues)
    for sh in surfaces:
        if not sh.exists:
            has_error = True
            continue
        if not sh.writable:
            # An existing-but-unwritable memory surface cannot satisfy a
            # write at all — that is a hard error, not a warning (PLA-259).
            has_error = True
            continue
        if sh.issues:
            has_warn = True
    if has_error:
        return _ERROR
    if has_warn:
        return _WARN
    return _OK


def _rollup_report_overall(agents: tuple[AgentHealth, ...]) -> str:
    """Roll per-agent labels: error > warn > ok."""
    if any(a.overall == _ERROR for a in agents):
        return _ERROR
    if any(a.overall == _WARN for a in agents):
        return _WARN
    return _OK


def _build_summary_text(agents: tuple[AgentHealth, ...], overall: str) -> str:
    """One-paragraph operator-friendly summary the CLI default mode
    can print without re-implementing rendering logic.
    """
    if not agents:
        return "no agents configured — add agents.<name> or agent_defaults to kairix.config.yaml"
    counts = {_OK: 0, _WARN: 0, _ERROR: 0}
    for a in agents:
        counts[a.overall] = counts.get(a.overall, 0) + 1
    return (
        f"{len(agents)} agent(s) checked — overall={overall} "
        f"(ok={counts[_OK]} warn={counts[_WARN]} error={counts[_ERROR]})"
    )


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def _scope_to_agent_health(
    scope: AgentScope,
    *,
    agent_issues: tuple[str, ...] = (),
) -> AgentHealth:
    """Probe + classify one :class:`AgentScope`, returning :class:`AgentHealth`.

    ``agent_issues`` carries any agent-level issues already known
    (e.g. fallback-to-defaults synthesis warning) — they roll into
    the ``overall`` calculation alongside the per-surface issues.
    """
    surfaces = _probe_agent_surfaces(scope)
    overall = _rollup_overall(surfaces, agent_issues)
    return AgentHealth(
        name=scope.name,
        harness=scope.harness,
        surfaces=surfaces,
        overall=overall,
        issues=agent_issues,
    )


def _load_or_empty(config: dict[str, object] | None) -> dict[str, AgentScope]:
    """Wrap :func:`load_agent_scopes` so doctor never raises on bad
    config — invalid YAML / malformed surfaces collapse to an empty
    scope map and the issue surfaces via the synthesised report.
    """
    if config is None:
        return {}
    try:
        return load_agent_scopes(config)
    except (ValueError, TypeError) as exc:
        logger.warning("doctor failed to load agent scopes: %s", exc)
        return {}


def doctor_check_all(
    *,
    config: dict[str, object] | None = None,
) -> DoctorReport:
    """Walk every configured agent's scope, validate each surface,
    return :class:`DoctorReport`.

    Never raises — disk-IO errors become per-surface ``issues``
    entries; malformed config produces an empty report with
    ``overall="ok"`` and a summary noting "no agents configured".
    """
    scopes = _load_or_empty(config)
    if not scopes:
        return DoctorReport(
            agents=(),
            overall=_OK,
            summary_text=_build_summary_text((), _OK),
        )

    # First pass: probe each agent in isolation.
    healths: list[AgentHealth] = [_scope_to_agent_health(scope) for _, scope in sorted(scopes.items())]

    # Second pass: cross-agent overlap. Build a flat (agent, surface,
    # label) list and look for shared matching files.
    flat: list[tuple[str, SurfaceHealth, str]] = []
    for h in healths:
        flat.extend((h.name, sh, sh.label) for sh in h.surfaces)
    overlap_map = _flag_overlaps(flat)

    # Rebuild each AgentHealth with the overlap-augmented surfaces +
    # re-roll its overall label.
    rebuilt: list[AgentHealth] = []
    for h in healths:
        new_surfaces = _apply_overlap_issues(h.surfaces, overlap_map)
        new_overall = _rollup_overall(new_surfaces, h.issues)
        # If surface gained overlap issues but stayed populated, it's
        # at most a "warn". Apply the warn label explicitly so the
        # overlap shows up.
        if new_overall == _OK and any(sh.issues for sh in new_surfaces):
            new_overall = _WARN
        rebuilt.append(
            AgentHealth(
                name=h.name,
                harness=h.harness,
                surfaces=new_surfaces,
                overall=new_overall,
                issues=h.issues,
            ),
        )

    final_agents = tuple(rebuilt)
    overall = _rollup_report_overall(final_agents)
    return DoctorReport(
        agents=final_agents,
        overall=overall,
        summary_text=_build_summary_text(final_agents, overall),
    )


def _synthesise_unknown_agent_health(agent_name: str) -> AgentHealth:
    """Build an error AgentHealth for an agent with no config + no defaults."""
    return AgentHealth(
        name=agent_name,
        harness="",
        surfaces=(),
        overall=_ERROR,
        issues=(f"no config for {agent_name} — {_ONBOARD_REMEDIATION.format(name=agent_name)}",),
    )


def doctor_check_agent(
    agent_name: str,
    *,
    config: dict[str, object] | None = None,
) -> AgentHealth:
    """Validate a single agent's configured scope.

    Falls back to defaults synthesis (via :func:`get_agent_scope`)
    when no explicit ``agents.<name>`` entry exists; the doctor flags
    that case with a ``"warn"`` overall and the standard onboard
    suggestion in ``issues``.

    Never raises — if neither explicit config nor agent_defaults
    resolves a scope, returns an :class:`AgentHealth` with
    ``overall="error"`` and an actionable issue.
    """
    scopes = _load_or_empty(config)
    if agent_name in scopes:
        return _scope_to_agent_health(scopes[agent_name])

    # No explicit entry → try defaults synthesis. The resolver raises
    # ValueError when neither path resolves; convert to an
    # AgentHealth so the caller branches on .overall rather than
    # catching exceptions.
    try:
        scope = get_agent_scope(agent_name, config=config)
    except ValueError:
        return _synthesise_unknown_agent_health(agent_name)
    fallback_issue = _FALLBACK_MESSAGE.format(name=agent_name)
    return _scope_to_agent_health(scope, agent_issues=(fallback_issue,))
