"""End-to-end composed-path test — unified container (Plan 2, task 6).

Exercises the full composed production path for the Plan 2 cutover:

  docker compose up -d --wait
    → both services boot through their real healthchecks
    → real HTTP probe against the api process inside the unified container
    → real ``docker compose ps`` to assert the 2-service shape
       (previously 3: kairix + kairix-worker + neo4j; now 2: kairix + neo4j)
    → real ``docker exec`` to verify the kairix user uid (995)

This is the F48 sibling test for the unified-container capability — every
new top-level capability gets a ``tests/e2e/test_composed_<capability>_path.py``
that drives the real composed path end-to-end.

The test skips cleanly when the Docker daemon is unavailable (developer
laptop without Docker, CI Stage 2/3 jobs without docker-in-docker). It
runs in CI Stage 4.5 under ``pytest -m e2e``.

5-minute timeout covers cold image pulls + first-boot s6 init + healthcheck
``start_period`` (60s) settling on both services.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.e2e

# Compose project at the repo root — this is the file an operator runs.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_FILE = _REPO_ROOT / "docker-compose.yml"

# The healthcheck endpoint the supervised api process serves on port 8080
# inside the container; compose maps 127.0.0.1:8090 → container 8080.
_READY_URL = "http://127.0.0.1:8090/healthz/ready"

# Stable container name from docker-compose.yml (`container_name: app-kairix-1`).
_KAIRIX_CONTAINER = "app-kairix-1"

# Expected uid from the Dockerfile's ``useradd --uid 995`` (KF-4 regression
# guard). If this number changes, the host-side bind-mount ownership story
# in MCP-DEPLOYMENT.md breaks; flag at the test layer first.
_EXPECTED_UID = 995


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


_IMAGE_REGISTRY = "ghcr.io/three-cubes/kairix"
_IMAGE_TAG_SUFFIX = "test-local"
_LOCAL_IMAGE = f"{_IMAGE_REGISTRY}:{_IMAGE_TAG_SUFFIX}"


def _compose(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose -f <repo>/docker-compose.yml <args...>``.

    Surfaces stdout + stderr in the CalledProcessError so a failing compose
    step produces a diagnosable failure message rather than a bare returncode.
    Sets ``KAIRIX_IMAGE_TAG`` so compose picks the locally-built image instead
    of pulling from ghcr (the unified-container image isn't published until
    this PR merges).
    """
    import os as _os

    env = {**_os.environ, "KAIRIX_IMAGE_TAG": _IMAGE_TAG_SUFFIX}
    cmd = ["docker", "compose", "-f", str(_COMPOSE_FILE), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=check,
        env=env,
    )


@pytest.mark.docker
@pytest.mark.timeout(300)
def test_compose_up_yields_two_services_both_healthy() -> None:
    """Compose-up brings up exactly 2 services; api is reachable; uid is 995.

    Composed path: real ``docker compose up -d --wait`` against the local
    repo's compose file → both services pass their declared healthchecks
    → real HTTP GET against /healthz/ready → real ``docker compose ps``
    asserting service count → real ``docker exec id`` asserting uid.

    Sabotage-proof: tested by mutating ``_EXPECTED_UID`` to 996 — the
    ``id`` assertion fires and the test fails. Restored the constant.
    Also tested by mutating the expected service count to 3 — the
    ``compose ps`` assertion fires for the same reason.
    """
    if not _docker_available():
        pytest.skip(
            reason="docker daemon not reachable — composed unified-container "
            "E2E test requires a running docker engine. Run under CI Stage "
            "4.5 (`pytest -m e2e`) or locally with `colima start` / Docker "
            "Desktop running.",
        )

    # Build the kairix image locally and tag it under the registry prefix
    # compose expects, then KAIRIX_IMAGE_TAG (set inside _compose) picks it up
    # — no public-registry round-trip.
    subprocess.run(
        ["docker", "build", "-t", _LOCAL_IMAGE, "."],
        cwd=str(_REPO_ROOT),
        check=True,
    )

    # Compose mounts /run/secrets/kairix.env; on a bare runner this doesn't
    # exist. Create an empty stub — the E2E test asserts on process IDs +
    # healthcheck, not on any configured secrets.
    secrets_stub = Path("/run/secrets/kairix.env")
    if not secrets_stub.exists():
        secrets_stub.parent.mkdir(parents=True, exist_ok=True)
        secrets_stub.write_text("# test stub — empty\n")

    # docker compose up -d --wait: starts containers and blocks until each
    # service's healthcheck passes (or fails). Timeout generous enough to
    # cover an image pull on a cold CI runner.
    try:
        _compose("up", "-d", "--wait", timeout=240)
    except subprocess.CalledProcessError as e:
        # Surface compose logs so a failing boot is diagnosable.
        logs = _compose("logs", "--no-color", check=False, timeout=60)
        pytest.fail(
            "docker compose up --wait failed:\n"
            f"stdout:\n{e.stdout}\n"
            f"stderr:\n{e.stderr}\n"
            f"compose logs:\n{logs.stdout}\n{logs.stderr}",
        )

    try:
        # (1) Real HTTP probe — the supervised api process serves
        # /healthz/ready on port 8080 inside the container, mapped to
        # 127.0.0.1:8090 on the host by docker-compose.yml. A short retry
        # loop covers the gap between docker compose --wait returning
        # (container healthy per Docker's healthcheck) and the host-side
        # port forward being routable.
        last_err: Exception | None = None
        response = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = requests.get(_READY_URL, timeout=5)
                break
            except requests.RequestException as exc:
                last_err = exc
                time.sleep(2)
        assert response is not None, f"GET {_READY_URL} never returned within 30s: last error = {last_err!r}"
        assert response.status_code == 200, (
            f"GET {_READY_URL} returned {response.status_code}, expected 200. body: {response.text[:500]!r}"
        )

        # (2) docker compose ps --format json — assert exactly 2 services
        # (kairix + neo4j). Previously this was 3 (kairix, kairix-worker,
        # neo4j); Plan 2 task 4 dropped kairix-worker so the worker now
        # runs as an s6 child inside the kairix container.
        ps_proc = _compose("ps", "--format", "json", timeout=30)
        # ``docker compose ps --format json`` emits one JSON object per
        # service per line (NDJSON), not a JSON array. Parse line by line.
        services = [json.loads(line) for line in ps_proc.stdout.splitlines() if line.strip()]
        service_names = sorted(svc.get("Service", "") for svc in services if svc.get("Service"))
        assert service_names == ["kairix", "neo4j"], (
            f"expected exactly 2 services [kairix, neo4j], got {service_names}. "
            f"Plan 2 task 4 drops kairix-worker; if this assertion fires the "
            f"compose file regressed back to 3 services.\n"
            f"raw ps output:\n{ps_proc.stdout}"
        )

        # (3) docker exec app-kairix-1 id — assert uid=995. The Dockerfile
        # declares ``useradd --uid 995`` so host-side bind-mounted volume
        # writes land as kairix:kairix (995:985). Failing this assertion
        # means a Dockerfile regression (back to root) which silently
        # breaks bind-mount ownership.
        id_proc = subprocess.run(
            ["docker", "exec", _KAIRIX_CONTAINER, "id"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        assert f"uid={_EXPECTED_UID}" in id_proc.stdout, (
            f"expected uid={_EXPECTED_UID} in `docker exec {_KAIRIX_CONTAINER} id` output, got: {id_proc.stdout!r}"
        )
    finally:
        # Teardown: -v also drops the named volumes (kairix-data,
        # kairix-cache, neo4j-data) so a re-run starts clean. check=False
        # because we still want to clean up after a partial-boot failure.
        _compose("down", "-v", check=False, timeout=120)
