"""pytest-bdd loader for ``unified_container.feature``.

Plan 2 (unified container) is fully landed: the Dockerfile declares
``USER kairix`` uid 995, the s6 service definitions exist, and the
``docker-compose.yml`` ships two services (was three). The scenarios in
``unified_container.feature`` describe *current* deployment behaviour
(tag flipped from ``@future`` to ``@current``).

Why the scenarios stay decorated with ``@pytest.mark.skip``: each
scenario asserts a runtime fact about a live container (`docker compose
up -d`, `docker exec ... ps -ef`, `docker exec ... id`, SIGTERM
behaviour). Those facts need a real Docker daemon and a built image to
verify — the assertions belong in the integration tier, not the BDD
collector. The equivalent assertion battery runs in
``tests/integration/test_container_supervisor.py`` (Plan 2 Task 5),
which builds the image, runs ``docker compose up -d --wait``, and
checks the supervisor + uid + process-list invariants against the live
container.

This file keeps the BDD scenarios discoverable so the feature reads as
the canonical user-facing description of the unified-container
contract, while delegating the actual mechanical proof to the
integration tier where ``docker`` is available.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "unified_container.feature")

# F11 rationale: each @pytest.mark.skip(reason=...) below points at the
# integration test that asserts the same invariant against a live
# container. Skips follow the F21 affordance template (fix / next / run
# markers) so a developer reading the test sees the path to verifying
# the invariant without leaving the file.


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "requires docker daemon to verify `docker compose up -d` + `docker compose ps` output. "
        "fix: assertion runs in tests/integration/test_container_supervisor.py (Plan 2 Task 5) "
        "which builds the image and checks the live 2-service compose shape. "
        "next: drop the skip once the BDD tier wires a docker fixture (Plan 2 follow-up). "
        "run: pytest tests/integration/test_container_supervisor.py -v (requires docker)."
    )
)
@scenario(FEATURE, "docker compose up brings up 2 services (was 3)")
def test_docker_compose_up_brings_up_two_services():
    """Body populated by @scenario from the .feature file.

    Skipped at the BDD tier because the assertion needs a live Docker
    daemon — the equivalent integration test runs the proof.
    """


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "requires docker daemon to inspect process list inside the running container. "
        "fix: assertion runs in tests/integration/test_container_supervisor.py (Plan 2 Task 5) "
        "which builds the image and asserts s6-supervisor + kairix-api + kairix-worker visible in ps -ef. "
        "next: drop the skip once the BDD tier wires a docker fixture (Plan 2 follow-up). "
        "run: pytest tests/integration/test_container_supervisor.py -v (requires docker)."
    )
)
@scenario(FEATURE, "kairix container runs both api + worker via s6")
def test_kairix_container_runs_both_api_and_worker_via_s6():
    """Body populated by @scenario from the .feature file.

    Skipped at the BDD tier because the assertion needs a live Docker
    daemon — the equivalent integration test runs the proof.
    """


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "requires docker daemon to run `docker exec <container> id`. "
        "fix: assertion runs in tests/integration/test_container_supervisor.py (Plan 2 Task 5) "
        "which builds the image and asserts uid=995 inside the running container. "
        "next: drop the skip once the BDD tier wires a docker fixture (Plan 2 follow-up). "
        "run: pytest tests/integration/test_container_supervisor.py -v (requires docker)."
    )
)
@scenario(FEATURE, "Container runs as the kairix user (uid 995)")
def test_container_runs_as_kairix_user_uid_995():
    """Body populated by @scenario from the .feature file.

    Skipped at the BDD tier because the assertion needs a live Docker
    daemon — the equivalent integration test runs the proof.
    """


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "requires docker daemon and a host bind mount to check file ownership on the host volume. "
        "fix: assertion runs in tests/integration/test_container_supervisor.py (Plan 2 Task 5) "
        "which writes to a bind-mounted volume and stats the resulting file's uid/gid. "
        "next: drop the skip once the BDD tier wires a docker fixture with a bind mount (Plan 2 follow-up). "
        "run: pytest tests/integration/test_container_supervisor.py -v (requires docker)."
    )
)
@scenario(FEATURE, "Files written to the volume land as kairix:kairix on host")
def test_files_written_to_volume_land_as_kairix_on_host():
    """Body populated by @scenario from the .feature file.

    Skipped at the BDD tier because the assertion needs a live Docker
    daemon — the equivalent integration test runs the proof.
    """


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "requires docker daemon to deliver SIGTERM via `docker stop` and observe exit codes. "
        "fix: assertion runs in tests/integration/test_container_supervisor.py (Plan 2 Task 5) "
        "which runs `docker stop` and asserts both processes exit within 30s. "
        "next: drop the skip once the BDD tier wires a docker fixture (Plan 2 follow-up). "
        "run: pytest tests/integration/test_container_supervisor.py -v (requires docker)."
    )
)
@scenario(FEATURE, "SIGTERM to the container shuts both processes gracefully")
def test_sigterm_to_container_shuts_both_processes_gracefully():
    """Body populated by @scenario from the .feature file.

    Skipped at the BDD tier because the assertion needs a live Docker
    daemon — the equivalent integration test runs the proof.
    """
