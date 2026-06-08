"""Unified container supervisor — both processes run as uid 995.

Plan 2 Task 5 (`docs/architecture/unified-container-supervisor.md`).
Asserts the unified image:

  1. Boots via ``docker compose up -d --wait`` (s6 is pid 1).
  2. Runs BOTH the api (``kairix mcp serve``) and the worker
     (``kairix worker run``) processes inside the ``app-kairix-1``
     container — proving the s6 supervisor is wired up.
  3. Runs as the ``kairix`` system user (uid 995), matching the
     host convention so bind-mounted volume contents are owned by
     the right uid:gid on the host.

The test is HEAVY (image build + compose up — multi-minute), so it
carries both ``@pytest.mark.integration`` and ``@pytest.mark.docker``.
It is excluded from Stage 2 (unit) and only fires under
``pytest -m integration`` plus the docker marker. Skips gracefully
when ``docker`` is not on PATH or the daemon is unreachable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTAINER_NAME = "app-kairix-1"
# Compose file references ``ghcr.io/three-cubes/kairix:${KAIRIX_IMAGE_TAG:-main}``.
# The test builds the image locally and tags it under that exact registry path
# with a sentinel tag, then sets KAIRIX_IMAGE_TAG so compose picks it up
# without trying to pull from ghcr (the unified-container image isn't published
# to ghcr until the PR merges).
_IMAGE_REGISTRY = "ghcr.io/three-cubes/kairix"
_IMAGE_TAG_SUFFIX = "test-local"
_IMAGE_TAG = f"{_IMAGE_REGISTRY}:{_IMAGE_TAG_SUFFIX}"


def _docker_available() -> bool:
    """``docker`` binary on PATH AND the daemon is reachable."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


@pytest.mark.integration
@pytest.mark.docker
@pytest.mark.timeout(300)
def test_container_runs_both_api_and_worker_as_uid_995() -> None:
    """The unified container supervises api + worker as uid 995.

    Sabotage-proof: locally mutated the assertion to expect
    ``id_out == "0"`` (root) — the test failed against the unified
    image (which is uid 995). Reverting the assertion restored green.
    Likewise mutating the ps-output assertion to ``"mcp-serve-NOPE"``
    failed as expected. Both proofs ran against a locally-built
    ``kairix:test`` image.

    F11 skip rationale: this test requires a docker daemon AND
    multi-minute image-build wall-clock budget, so it skips
    cleanly when docker is unavailable rather than failing a
    laptop run.
    """
    if not _docker_available():
        pytest.skip(
            reason="docker daemon not reachable — unified-container "
            "supervisor test requires a running docker engine "
            "(install Docker Desktop / dockerd, or run in CI Stage 3+)",
        )

    # Build the kairix image from the repo root Dockerfile.
    subprocess.run(
        ["docker", "build", "-t", _IMAGE_TAG, "."],
        cwd=str(_REPO_ROOT),
        check=True,
    )

    # Compose mounts /run/secrets/kairix.env into both kairix + neo4j; on a
    # bare runner this path doesn't exist. Create an empty stub so compose
    # can mount it; the empty env file is fine for the supervisor probe (the
    # test asserts on process IDs + uid, not on any configured secrets).
    # Skip on platforms where /run is read-only (macOS), letting Linux CI
    # carry the end-to-end check.
    secrets_stub = Path("/run/secrets/kairix.env")
    if not secrets_stub.exists():
        try:
            secrets_stub.parent.mkdir(parents=True, exist_ok=True)
            secrets_stub.write_text("# test stub — empty\n")
        except OSError as exc:
            pytest.skip(
                reason=f"cannot create /run/secrets stub ({exc}); "
                "this integration test runs on Linux CI where /run is writable. "
                "fix: re-run under Linux (CI Stage 3) or skip-locally as designed.",
            )

    # Spin up via compose so neo4j comes along for the ride and the
    # healthcheck gating matches what operators see in production.
    # Pass KAIRIX_IMAGE_TAG so compose picks the locally-built image we just
    # tagged (under the ghcr.io/three-cubes/kairix prefix) instead of pulling
    # from the public registry.
    import os as _os

    env = {**_os.environ, "KAIRIX_IMAGE_TAG": _IMAGE_TAG_SUFFIX}
    subprocess.run(
        ["docker", "compose", "up", "-d", "--wait"],
        cwd=str(_REPO_ROOT),
        check=True,
        env=env,
    )
    try:
        ps_proc = subprocess.run(
            ["docker", "exec", _CONTAINER_NAME, "ps", "-ef"],
            capture_output=True,
            text=True,
            check=True,
        )
        ps_out = ps_proc.stdout
        assert "mcp serve" in ps_out, f"api process missing from container ps -ef:\n{ps_out}"
        assert "worker run" in ps_out, f"worker process missing from container ps -ef:\n{ps_out}"
        assert ("s6-supervisor" in ps_out) or ("/init" in ps_out), (
            f"s6 supervisor not visible as pid 1 in container ps -ef:\n{ps_out}"
        )

        id_proc = subprocess.run(
            ["docker", "exec", _CONTAINER_NAME, "id", "-u"],
            capture_output=True,
            text=True,
            check=True,
        )
        id_out = id_proc.stdout.strip()
        assert id_out == "995", f"container must run as kairix uid 995, got uid={id_out!r}"
    finally:
        # Teardown shouldn't break the test if it succeeds — best-effort.
        import os as _os

        env = {**_os.environ, "KAIRIX_IMAGE_TAG": _IMAGE_TAG_SUFFIX}
        subprocess.run(
            ["docker", "compose", "down", "-v"],
            cwd=str(_REPO_ROOT),
            check=False,
            env=env,
        )
