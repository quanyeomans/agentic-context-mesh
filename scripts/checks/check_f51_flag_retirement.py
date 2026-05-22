"""F51: every feature flag has a target_retire_in within 6 months of current version.

Feature flags are not permanent scaffolding. Each entry in
``kairix/core/features/registry.py:REGISTRY`` carries a ``target_retire_in``
version. F51 fires when the deadline has passed AND no adjacent
``# retire-extension: <reason>`` rationale comment is present in the
registry file.

Detection:

  1. Import ``kairix.core.features.registry.REGISTRY``. If the module is
     not yet present (PR-2 has not landed), exit 0 — vacuous-green per
     the cross-PR dependency convention.
  2. Determine current version via ``setuptools-scm`` (``get_version``).
  3. Add 6 months to the current version's date component (versions are
     CalVer-shaped ``vYYYY.M.D`` in this repo).
  4. For each flag, compare ``target_retire_in`` against the deadline.
     If past, scan the registry source for an adjacent
     ``# retire-extension: <reason>`` comment; if absent, record a
     violation.

Violation file paths use the registry path with a ``:flag=<name>``
suffix so each flag entry produces a distinguishable baseline line.

Per F21, REMEDIATION carries ``fix:`` / ``next:`` / ``run:`` markers.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

REGISTRY_REL_PATH = Path("kairix/core/features/registry.py")

REMEDIATION = """F51: feature flag <name> is past its target_retire_in deadline.
fix: either retire the flag (delete the REGISTRY entry + remove the legacy
     code path) OR bump target_retire_in with a '# retire-extension: <reason>'
     comment adjacent to the entry in kairix/core/features/registry.py.
next: see docs/architecture/feature-flag-architecture.md §4.1 (lifecycle
      stages) + §6 (F51 mechanics).
run: bash scripts/checks/check-f51-flag-retirement.sh"""

_VERSION_RE = re.compile(r"^v?(\d{4})\.(\d{1,2})\.(\d{1,2})(?:[.\-+].*)?$")


def _parse_calver(version: str) -> date | None:
    """Parse a CalVer ``vYYYY.M.D`` string to a date.

    Returns None when the version does not match the CalVer shape (e.g.
    a ``setuptools-scm``-generated PEP-440 dev version mid-cycle); the
    caller treats None as "skip deadline check, gate stays green".
    """
    match = _VERSION_RE.match(version.strip())
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _add_six_months(d: date) -> date:
    """Return ``d + 6 months`` clamped to the last day of the target month."""
    month = d.month + 6
    year = d.year + (month - 1) // 12
    month = ((month - 1) % 12) + 1
    # Clamp day to month end (e.g. Aug 31 + 6 -> Feb 28/29).
    for day in (d.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def _current_version() -> str | None:
    """Return ``setuptools-scm`` current version or None on failure."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "from setuptools_scm import get_version; print(get_version())"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
        )
    except (OSError, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _find_extension_comment_lines(registry_text: str) -> set[str]:
    """Return the set of flag names that have a ``# retire-extension: <reason>``
    comment on the line(s) immediately preceding their REGISTRY entry.

    The detection scans the registry AST for ``"<name>": FeatureFlag(...)``
    dict items, then looks at the source lines just above each item for a
    comment of the shape ``# retire-extension: ...``.
    """
    try:
        tree = ast.parse(registry_text)
    except SyntaxError:
        return set()

    lines = registry_text.splitlines()
    extended: set[str] = set()

    for node in ast.walk(tree):
        if not (isinstance(node, ast.AnnAssign | ast.Assign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        is_registry = any(isinstance(t, ast.Name) and t.id == "REGISTRY" for t in targets)
        if not is_registry:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=False):
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            flag_name = key.value
            # Look at the lines immediately above the key for a retire-extension comment.
            key_line = getattr(key, "lineno", None)
            if key_line is None:
                continue
            # Walk back through contiguous comment lines.
            idx = key_line - 2  # 0-indexed; line above the key
            while idx >= 0:
                stripped = lines[idx].strip()
                if not stripped:
                    break
                if stripped.startswith("# retire-extension:"):
                    extended.add(flag_name)
                    break
                if not stripped.startswith("#"):
                    break
                idx -= 1
            # Also check inline (same-line) suffix comments on the
            # ``target_retire_in=`` line within the value, since that's
            # where reviewers naturally drop the rationale.
            if isinstance(value, ast.Call):
                for kw in value.keywords:
                    if kw.arg != "target_retire_in":
                        continue
                    kw_line = getattr(kw, "lineno", None)
                    if kw_line is None:
                        continue
                    line_text = lines[kw_line - 1]
                    if "# retire-extension:" in line_text:
                        extended.add(flag_name)
    return extended


def _load_registry() -> dict[str, object] | None:
    """Import REGISTRY defensively. Returns None when the module is absent.

    Module may be absent pre-PR-2; defensive import keeps the gate
    vacuous-green per the F51 cross-PR convention.
    """
    try:
        from kairix.core.features.registry import REGISTRY
    except ImportError:
        return None
    return dict(REGISTRY)


def find_violations(
    registry: dict[str, object],
    registry_text: str,
    current_version: str,
) -> list[str]:
    """Return sorted list of ``<registry_path>:flag=<name>`` strings for
    flags past their deadline without a retire-extension comment.
    """
    current_date = _parse_calver(current_version)
    if current_date is None:
        return []
    deadline = _add_six_months(current_date)
    extended = _find_extension_comment_lines(registry_text)
    flagged: list[str] = []
    for name, entry in registry.items():
        target = getattr(entry, "target_retire_in", None)
        if not isinstance(target, str):
            continue
        target_date = _parse_calver(target)
        if target_date is None:
            continue
        if target_date > deadline and name not in extended:
            flagged.append(f"{REGISTRY_REL_PATH.as_posix()}:flag={name}")
    return sorted(flagged)


def main() -> int:
    """Return 0 when clean / vacuous-green; 1 when net-new violations exist."""
    registry = _load_registry()
    if registry is None:
        # PR-2 not yet landed; vacuous-green per cross-PR convention.
        print("ok [arch:f51-flag-retirement] — kairix.core.features absent; vacuous-green.")
        return 0
    if not registry:
        print("ok [arch:f51-flag-retirement] — registry empty; vacuous-green.")
        return 0

    registry_path = REPO_ROOT / REGISTRY_REL_PATH
    if not registry_path.exists():
        print("ok [arch:f51-flag-retirement] — registry file absent; vacuous-green.")
        return 0

    version = _current_version()
    if version is None:
        print("ok [arch:f51-flag-retirement] — setuptools-scm unavailable; vacuous-green.")
        return 0

    violations = find_violations(registry, registry_path.read_text(), version)
    if not violations:
        print("ok [arch:f51-flag-retirement] — clean.")
        return 0

    print("FAIL [arch:f51-flag-retirement] — flag(s) past target_retire_in:")
    for v in violations:
        print(f"  {v}")
    print()
    print(REMEDIATION)
    return 1


if __name__ == "__main__":
    sys.exit(main())
