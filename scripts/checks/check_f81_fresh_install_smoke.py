"""F81: the CI fresh-install smoke exists and stays wired.

The smoke itself (onboarding tranche 3, 2026-06-11; EPIC #499 Phase 0
registration; choreography stage added EPIC #499 Phase 3) is
``scripts/checks/check-fresh-install-smoke.sh``: from a clean temp
directory it replays the README quick start — compose boot →
``/healthz/ready`` 200 → MCP initialize + tools/list handshake →
``GET /setup/`` 200 with the wizard flag ON → wizard choreography
(``POST /setup/folder/scan`` returns the HTMX scan partial; ``POST
/setup/key`` drives the form→redirect choreography) → BM25 search hit on
a seeded sample document. It runs in CI via
``.github/workflows/fresh-install-smoke.yml`` (it needs Docker and
minutes of wall-clock, so it is NOT a per-commit local gate).

This per-commit check is the structural half: it proves the smoke
cannot silently rot out of the pipeline. It fails when:

  1. ``scripts/checks/check-fresh-install-smoke.sh`` is missing, or
  2. ``.github/workflows/fresh-install-smoke.yml`` is missing, or
  3. the workflow no longer invokes the smoke script.

Binary presence check, no per-file baseline (same shape as F53's
operator-surface check).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _arch_lib import REPO_ROOT, gate

SMOKE_SCRIPT_REL = Path("scripts/checks/check-fresh-install-smoke.sh")
WORKFLOW_REL = Path(".github/workflows/fresh-install-smoke.yml")

REMEDIATION = """F81: the fresh-install smoke is missing or unwired.

The smoke is the only gate that proves a stranger's fresh install
boots: clean dir → compose up → /healthz/ready 200 → MCP handshake →
wizard 200 → BM25 search hit. If the script or its workflow drops out
of the tree, every fresh-install regression ships invisibly again
(the #469-#478 class).

fix: restore scripts/checks/check-fresh-install-smoke.sh AND
.github/workflows/fresh-install-smoke.yml, and keep the workflow's run
step invoking the script by its repo path.
next: re-run python3 scripts/checks/check_f81_fresh_install_smoke.py
to confirm the gate goes green.
run: KAIRIX_IMAGE_TAG=main bash scripts/checks/check-fresh-install-smoke.sh

Pass example: .github/workflows/fresh-install-smoke.yml
  - name: Run fresh-install smoke
    run: bash scripts/checks/check-fresh-install-smoke.sh

Forbidden example: deleting the workflow (or pointing it at a renamed
script without updating the reference) — the smoke still exists on
disk but never runs, so a broken quick start reaches operators before
anyone notices.

See EPIC #499 Phase 0 for the registration rationale."""


def collect_violations(repo_root: Path = REPO_ROOT) -> set[Path]:
    """Return synthetic repo-relative paths for each missing/unwired leg.

    The synthetic entries name the missing artefact so the failure
    output reads as an inventory, mirroring F68's <Protocol>.<method>
    convention.
    """
    violations: set[Path] = set()
    smoke_script = repo_root / SMOKE_SCRIPT_REL
    workflow = repo_root / WORKFLOW_REL

    if not smoke_script.is_file():
        violations.add(SMOKE_SCRIPT_REL)
    if not workflow.is_file():
        violations.add(WORKFLOW_REL)
    elif SMOKE_SCRIPT_REL.name not in workflow.read_text(encoding="utf-8"):
        violations.add(Path(f"{WORKFLOW_REL}::does-not-invoke-{SMOKE_SCRIPT_REL.name}"))
    return violations


def main() -> int:
    return gate("f81-fresh-install-smoke", collect_violations(), REMEDIATION)


if __name__ == "__main__":
    sys.exit(main())
