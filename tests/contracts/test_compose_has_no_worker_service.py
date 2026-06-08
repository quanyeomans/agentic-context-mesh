"""Contract C2 (Plan 2 — unified container) — compose drops kairix-worker.

Plan 2 unifies the api and worker processes inside a single supervised
container. Task 4 landed the docker-compose refactor: the
`kairix-worker` service is gone and the unified `kairix` service carries
the combined memory limit (3g + 1g → 4g).

This contract test pins both invariants:

  - `kairix-worker:` MUST NOT appear in `docker-compose.yml`
  - `memory: 4g` MUST appear in `docker-compose.yml`

Flipped GREEN after Plan 2 Task 4; the xfail decorator was removed in
Plan 2 Task 7 (close-out). See:
docs path — Plans/2026-06-07-2-unified-container-supervisor.md §Task 4.
"""

from pathlib import Path

import pytest


@pytest.mark.contract
def test_compose_drops_kairix_worker_service() -> None:
    """docker-compose.yml drops kairix-worker; unified kairix has memory: 4g.

    Plan 2 Contract C2 regression guard.
    """
    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    content = compose.read_text()
    assert "kairix-worker:" not in content
    assert "memory: 4g" in content
