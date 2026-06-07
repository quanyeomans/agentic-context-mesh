"""Contract C3 (Plan 2 — unified container supervisor).

Both s6 service directories ship with the repo so the Docker build can
``COPY`` them into the image. This is the structural pre-condition that
lets the unified ``kairix`` container run both api + worker under a
single s6 supervisor.

Plan 2 Task 2 landed the s6 service definitions; the xfail decorator was
removed in Plan 2 Task 7 (close-out).

Sabotage-proof: if a future edit ships only the api ``run`` script
(missing ``kairix-worker/run``), the second ``.exists()`` assertion fails
and the contract goes RED — exactly the regression we want to block.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.contract
def test_s6_service_dirs_exist_in_repo() -> None:
    """Both s6 service dirs ship with the repo so the Docker build can COPY them."""
    repo = Path(__file__).resolve().parents[2]
    api_run = repo / "docker/s6/services/kairix-api/run"
    worker_run = repo / "docker/s6/services/kairix-worker/run"

    assert api_run.exists(), f"missing s6 api run script: {api_run}"
    assert worker_run.exists(), f"missing s6 worker run script: {worker_run}"

    api_run_text = api_run.read_text()
    worker_run_text = worker_run.read_text()
    assert "kairix mcp serve" in api_run_text, "api run script must exec `kairix mcp serve`"
    assert "kairix worker run" in worker_run_text, "worker run script must exec `kairix worker run`"
