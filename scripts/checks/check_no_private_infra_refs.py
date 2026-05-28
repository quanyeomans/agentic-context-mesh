"""F73: No private infrastructure references in committed code, docs, or tests.

Public-repo hygiene: specific Azure resource names, internal hostnames,
and private sibling-repo names must not appear in kairix. Generic
placeholders document the shape (``<your-key-vault-name>``,
``<your-resource-group>``, ``example.com``) so external readers can
follow the docs and operators of any deployment can swap in their own
values.

This rule complements F32 (real first / surname / org names in fixtures);
F73 catches infrastructure identifiers that F32's name-list shape
doesn't fit.

Detection scope:

- ``kairix/**/*.py``        — production code
- ``scripts/**/*.{py,sh}``  — pre-commit / CI / operator scripts
- ``tests/**/*.py``         — fixtures + assertions
- ``tests/bdd/**/*.feature``— Gherkin scenarios
- ``docs/**/*.md``          — user-facing documentation
- ``CLAUDE.md``             — agent / contributor read-first guide
- top-level ``*.yaml`` / ``*.yml`` / ``*.toml`` — repo config

Detection signal: a curated set of compiled regex patterns covering
historical leaks. The set is intentionally narrow — each pattern was
either already in the source tree at the F73 introduction commit and
paid down, or names a private resource the user has flagged for leak
prevention.

Generic placeholders that are explicitly OK:

- ``<your-vm-name>`` / ``<your-key-vault-name>`` / ``<your-resource-group>``
- ``<your-storage-account>`` / ``<your-subscription>``
- ``example.com`` / ``alice@example.com`` / ``bob@example.com``
- ``Acme`` / ``Example Corp``

These never match ``_PATTERNS`` so they pass the filter trivially.

Baseline at ``.architecture/baseline/no-private-infra-refs-files.txt``
grandfathers pre-existing offenders so the rule lands without forcing
a sweep. F50 blocks net-new files from accreting baseline debt.

Why this rule exists: kairix is a public repository. Specific Azure
resource names (``vm-openclaw``, ``kv-tc-agents``, ``stagents001``),
internal hostnames (``ssh.threecubes.ai``), and private sibling-repo
URLs (``kairix-pro-platform``, ``tc-agent-zone``) aid reconnaissance
of the operator's deployment and leak organisational shape. Applying
generic placeholders preserves the documentation value while removing
the leak.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = ROOT / ".architecture" / "baseline" / "no-private-infra-refs-files.txt"

# Compiled patterns covering the leak shapes the W1+W3 sweeps paid down.
# Each pattern targets a specific shape; the named comment alongside the
# pattern records what placeholder to use when refactoring.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Specific Azure VM / Key Vault / storage account names that map to
    # the user's private deployment. Refactor to ``<your-vm-name>`` /
    # ``<your-key-vault-name>`` / ``<your-storage-account>``.
    ("azure-vm-name", re.compile(r"\bvm-openclaw\b")),
    ("azure-kv-name", re.compile(r"\bkv-tc-agents\b")),
    ("azure-storage-account", re.compile(r"\bstagents001\b")),
    # Azure resource group name. Refactor to ``<your-resource-group>``.
    ("azure-resource-group", re.compile(r"\bRG-AGENTS-CORE\b")),
    # Resource-lock / data-disk naming patterns scoped to the deployment.
    # Refactor narratives to describe the lock SHAPE, not the specific
    # name (``lock-nodelete-<resource>`` is the generic form).
    ("azure-datadisk-pattern", re.compile(r"\bdatadisk-vm-openclaw\b")),
    # Internal hostnames / subdomains / email domains. Refactor to
    # ``example.com`` for docs / ``<your-ssh-host>`` for runbooks.
    # Catches both ``ssh.threecubes.ai`` (subdomain) and
    # ``dan@threecubes.io`` (bare-domain email).
    ("internal-hostname", re.compile(r"\bthreecubes\.(?:ai|io)\b")),
    # Private sibling-repo references. The architectural concepts they
    # describe (two-scope architecture, Wave 0 paydown) stand alone;
    # the cross-repo URL/issue pointers add no public-reader value.
    ("private-sibling-repo", re.compile(r"\b(?:kairix-pro-platform|tc-agent-zone|tc-agents-zone)\b")),
)

# Self-exempt: the detector + its test file embed the patterns by
# definition. The W3 redaction commit also threads the architectural
# concepts through docs; any new file referenced here demands a PR-
# description rationale.
EXEMPT_FILES = frozenset(
    {
        "scripts/checks/check_no_private_infra_refs.py",
        "tests/checks/test_no_private_infra_refs.py",
    }
)

# Per-scope file selection. The detector walks every tracked file and
# includes it only if its repo-relative path matches at least one scope.
_SCOPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("kairix/", (".py",)),
    ("scripts/", (".py", ".sh")),
    ("tests/", (".py", ".feature")),
    ("docs/", (".md",)),
)

# Top-level files always in scope regardless of directory prefix.
_TOP_LEVEL_FILES: frozenset[str] = frozenset({"CLAUDE.md", "README.md", "CONTRIBUTING.md"})

REMEDIATION = """Refactor private infrastructure references to generic placeholders — to pass.

fix: replace the specific Azure resource name / internal hostname /
     private-repo reference with a generic placeholder that documents
     the SHAPE without naming the operator's deployment:
       - VMs:                     <your-vm-name>
       - Key Vaults:              <your-key-vault-name>
       - Storage accounts:        <your-storage-account>
       - Resource groups:         <your-resource-group>
       - Subscriptions:           <your-subscription>
       - Hostnames / SSH targets: example.com / <your-ssh-host>
       - Sibling repos:           drop the cross-repo URL; describe the
                                  architectural concept by name
next: re-run ``python3 scripts/checks/check_no_private_infra_refs.py``
to confirm the gate goes green.
run: bash scripts/safe-commit.sh "refactor(privacy): redact <what> from <where>"

Pass example:
  # docs/operations/runbooks/how-to-resolve-secrets.md
  Set ``KAIRIX_KV_NAME=<your-key-vault-name>`` before invoking ``kairix probe-config``.

Forbidden example:
  # docs/operations/runbooks/how-to-resolve-secrets.md
  Set ``KAIRIX_KV_NAME=kv-tc-agents`` before invoking ``kairix probe-config``.
"""


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


def _scan_file(path: Path, rel: str) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    hits: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for label, pattern in _PATTERNS:
            match = pattern.search(line)
            if match is not None:
                hits.append(f"{rel}:{lineno}: private-infra ref ({label}): {match.group(0)!r}")
    return hits


def main() -> int:
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
        hits = _scan_file(path, rel)
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
            print("ok [arch:no-private-infra-refs]")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
