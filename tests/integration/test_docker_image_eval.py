"""Smoke test: ``kairix eval`` runs inside the deployed docker image.

Plan B-parity Week 5 Stream C — packaging signal for the conversation
eval suite runner. Confirms three things in one round-trip:

  1. The image carries ``reference-library/conversations/`` at the
     stable path documented in ``docs/operations/MCP-DEPLOYMENT.md``
     (``/opt/kairix/reference-library/conversations/<corpus>``).
  2. The entrypoint passes the ``eval`` arg through to the
     ``kairix eval`` CLI (not the legacy ``benchmark-reflib`` mode).
  3. The CLI emits valid JSON on stdout when ``--json`` is set, so
     downstream cron / CI wrappers can pipe the result into ``jq``.

The test is opt-in via either:

  - ``KAIRIX_DOCKER_TEST_IMAGE=<image:tag>`` — point at a pre-built
    image (CI builds the image in an earlier stage and exports the
    tag), OR
  - The local docker daemon is up AND a ``kairix`` image tag is
    discoverable via ``docker image inspect kairix:latest``.

When neither is true the test skips with a documented reason (F11).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

# Stable path inside the image — matches the ``COPY reference-library/``
# line in the Dockerfile and the operator doc in MCP-DEPLOYMENT.md.
# If the Dockerfile stops copying ``conversations/`` this test fails
# at the ``docker run`` step (no such path inside the container) which
# is the failure signal we want.
_IMAGE_CORPUS_PATH = "/opt/kairix/reference-library/conversations/engagement-alpha"


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


def _resolve_image_tag() -> str | None:
    """Return the kairix image tag to test, or None when none usable."""
    env_tag = os.environ.get("KAIRIX_DOCKER_TEST_IMAGE")
    if env_tag:
        return env_tag
    # Fall back to a locally-built tag — common for developer-loop runs.
    probe = subprocess.run(
        ["docker", "image", "inspect", "kairix:latest"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    if probe.returncode == 0:
        return "kairix:latest"
    return None


@pytest.mark.integration
@pytest.mark.docker
def test_docker_run_kairix_eval_emits_valid_json() -> None:
    """``docker run <image> eval <corpus> --json`` round-trips valid JSON.

    Sabotage-proof: mutated ``_IMAGE_CORPUS_PATH`` to
    ``/opt/kairix/reference-library/conversations/engagement-missing``
    locally and re-ran the test — ``docker run`` exits non-zero (path
    not found inside the image), the JSON parse never happens, and the
    test fails as expected. Restored the constant.
    """
    if not _docker_available():
        pytest.skip(
            reason="docker daemon not reachable — packaging smoke test "
            "requires a running docker engine + a kairix image "
            "(set KAIRIX_DOCKER_TEST_IMAGE or `docker build -t kairix:latest .`)",
        )
    image = _resolve_image_tag()
    if image is None:
        pytest.skip(
            reason="no kairix image found — set KAIRIX_DOCKER_TEST_IMAGE "
            "to a pre-built tag or run `docker build -t kairix:latest .`",
        )

    proc = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            image,
            "eval",
            _IMAGE_CORPUS_PATH,
            "--json",
        ],
        capture_output=True,
        timeout=300,
        check=False,
    )

    # The CLI may exit non-zero on regression (--regression-against), but
    # a packaging smoke test asks a narrower question: did the entrypoint
    # dispatch to ``kairix eval`` AND did the CLI emit JSON? Surface the
    # captured stderr in the assertion message so a packaging regression
    # produces a useful failure rather than a bare ``AssertionError``.
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, f"docker run exited {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"

    # Strip any leading log lines from the entrypoint (e.g. ``Warming
    # kairix caches...``) — the JSON payload is the last contiguous
    # block of stdout that parses as a single JSON object.
    payload = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    parsed = json.loads(payload)
    assert isinstance(parsed, dict), f"expected a JSON object on stdout, got {type(parsed).__name__}: {parsed!r}"
