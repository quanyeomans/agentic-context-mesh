"""Token-pattern scanner with externalised pattern source.

The canonical pattern source is org config in the three-cubes org,
single-sourced for both CI and local dev:

  * CI reads the org SECRET ``PRIVATE_INFRA_PATTERNS`` into the
    ``PRIVATE_INFRA_PATTERNS`` env var (wired in ``.github/workflows/ci.yml``).
  * Local dev reads the org VARIABLE ``PRIVATE_INFRA_PATTERNS``
    (org-member-readable) via ``gh variable get`` —
    ``eval "$(bash scripts/fetch-fitness-config.sh)"`` to export it,
    or ``make fitness-config`` to cache it to the fallback file.

Pattern definitions are loaded at runtime from, in order:

  * ``PRIVATE_INFRA_PATTERNS`` env var (CI secret, or local export from the
    org variable via ``scripts/fetch-fitness-config.sh``).
  * ``.private-infra-patterns`` file at repo root (last-resort local
    fallback / cache; gitignored, template at
    ``.private-infra-patterns.example``). Prefer the org-variable fetch over
    hand-maintaining this file.

Each non-blank, non-comment line is one regex pattern. Optional
``label:regex`` shape lets the failure message name the category.
Empty pattern set = the detector is a no-op.

Scope: ``kairix/**/*.py``, ``scripts/**/*.{py,sh}``,
``tests/**/*.{py,feature}``, ``docs/**/*.md``, ``CLAUDE.md``,
``README.md``, ``CONTRIBUTING.md``.

Baseline at ``.architecture/baseline/no-private-infra-refs-files.txt``.
F50 blocks net-new files from accreting violations.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / ".architecture" / "baseline" / "no-private-infra-refs-files.txt"
PATTERNS_FILE = ROOT / ".private-infra-patterns"
PATTERNS_ENV_VAR = "PRIVATE_INFRA_PATTERNS"

EXEMPT_FILES = frozenset(
    {
        "scripts/checks/check_no_private_infra_refs.py",
        "tests/checks/test_no_private_infra_refs.py",
    }
)

_SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kairix/", (".py",)),
    ("scripts/", (".py", ".sh")),
    ("tests/", (".py", ".feature")),
    ("docs/", (".md",)),
)

_TOP_LEVEL_FILES: frozenset[str] = frozenset({"CLAUDE.md", "README.md", "CONTRIBUTING.md"})

REMEDIATION = """Pattern matched — refactor to a generic placeholder.

fix: replace the matched string with a generic placeholder
     (``<your-vm-name>``, ``<your-key-vault-name>``,
     ``<your-resource-group>``, ``example.com``, etc.).
next: re-run ``python3 scripts/checks/check_no_private_infra_refs.py``
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "<verb>(<scope>): <what>"

Pass example:
  Set ``KAIRIX_KV_NAME=<your-key-vault-name>`` before invoking the CLI.

Forbidden example:
  Set ``KAIRIX_KV_NAME=<a literal that matches the loaded pattern set>``.
"""


def _compile_patterns(raw: str) -> list[tuple[str, re.Pattern[str]]]:
    """Parse a newline-separated pattern source into compiled regexes.

    Each non-blank, non-``#`` line is one pattern. Optional ``label:regex``
    shape lets the failure message name the category. Lines whose regex
    fails to compile are reported as warnings and skipped.
    """
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for lineno, line in enumerate(raw.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped and not stripped.startswith("\\"):
            label, _, body = stripped.partition(":")
            label = label.strip() or f"pattern-{lineno}"
            pattern_text = body.strip()
        else:
            label = f"pattern-{lineno}"
            pattern_text = stripped
        if not pattern_text:
            continue
        try:
            patterns.append((label, re.compile(pattern_text)))
        except re.error as exc:
            print(
                f"WARN [arch:no-private-infra-refs]: invalid regex on line {lineno}: {exc}",
                file=sys.stderr,
            )
    return patterns


def _read_patterns_source() -> str:
    raw = os.environ.get(PATTERNS_ENV_VAR, "")
    if not raw and PATTERNS_FILE.exists():
        raw = PATTERNS_FILE.read_text(encoding="utf-8")
    return raw


def _load_patterns() -> list[tuple[str, re.Pattern[str]]]:
    return _compile_patterns(_read_patterns_source())


def _load_baseline() -> set[str]:
    if not BASELINE_FILE.exists():
        return set()
    return {
        line.strip()
        for line in BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _is_in_scope(rel: str) -> bool:
    if rel in _TOP_LEVEL_FILES:
        return True
    return any(rel.startswith(prefix) and rel.endswith(suffixes) for prefix, suffixes in _SCOPES)


def _scan_file(
    path: Path,
    rel: str,
    patterns: list[tuple[str, re.Pattern[str]]],
) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pattern in patterns:
            match = pattern.search(line)
            if match is not None:
                hits.append(f"{rel}:{lineno}: pattern match ({label}): {match.group(0)!r}")
    return hits


def main() -> int:
    patterns = _load_patterns()
    if not patterns:
        print(
            f"ok [arch:no-private-infra-refs] — no patterns loaded "
            f'(local dev: run `eval "$(bash scripts/fetch-fitness-config.sh)"` '
            f"to export ${PATTERNS_ENV_VAR} from the org variable, "
            f"or `make fitness-config` to cache {PATTERNS_FILE.name})."
        )
        return 0

    try:
        files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    except subprocess.CalledProcessError:
        print("FAIL no_private_infra_refs: could not enumerate tracked files", file=sys.stderr)
        return 1

    baseline = _load_baseline()
    net_new: list[str] = []
    matched_baseline_files: set[str] = set()

    for rel in files:
        if rel in EXEMPT_FILES:
            continue
        if not _is_in_scope(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        hits = _scan_file(path, rel, patterns)
        if not hits:
            continue
        if rel in baseline:
            matched_baseline_files.add(rel)
            continue
        net_new.extend(hits)

    stale = baseline - matched_baseline_files
    exit_code = 0
    if net_new:
        print("FAIL [arch:no-private-infra-refs] — net-new violations:", file=sys.stderr)
        for hit in net_new:
            print(f"  {hit}", file=sys.stderr)
        print(file=sys.stderr)
        print(REMEDIATION, file=sys.stderr)
        exit_code = 1
    if stale:
        print(
            f"FAIL [arch:no-private-infra-refs] — {len(stale)} baseline entries "
            "no longer hold violations; remove from baseline:",
            file=sys.stderr,
        )
        for rel in sorted(stale):
            print(f"  {rel}", file=sys.stderr)
        exit_code = 1
    if exit_code == 0:
        if baseline:
            print(f"ok [arch:no-private-infra-refs] — {len(baseline)} grandfathered file(s) still present in baseline.")
        else:
            print(f"ok [arch:no-private-infra-refs] — {len(patterns)} pattern(s) loaded.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
