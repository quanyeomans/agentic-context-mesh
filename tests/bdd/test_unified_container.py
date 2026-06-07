"""pytest-bdd loader for ``unified_container.feature``.

Plan 2 Task 1 — wires the unified-container BDD feature into the
pytest-bdd collector. Each scenario is tagged ``@future`` in the
.feature file and bound here with ``@pytest.mark.skip`` so the
collection succeeds and safe-commit stays green; the skip reasons
follow the F21 affordance template (fix / next / run markers) and
point at the subsequent Plan 2 tasks that land each implementation.

Step bodies will land alongside Tasks 2-6; this module only enrols
the scenarios for collection today.
"""

from pathlib import Path

import pytest
from pytest_bdd import scenario

FEATURE = str(Path(__file__).parent / "features" / "unified_container.feature")

# F11 rationale lives inline on each @pytest.mark.skip(reason=...) decorator
# below — the check_test_skip_rationale.py gate requires a literal string
# at the call site. Each reason follows the F21 template (fix / next / run
# markers) so a developer running the test sees the affordance without
# leaving the file.


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "future — Plan 2 Task 4; docker-compose refactor drops kairix-worker. "
        "fix: refactor docker-compose.yml so `docker compose ps` reports 2 services per Plan 2 Task 4. "
        "next: drop the @pytest.mark.skip once the compose refactor lands. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "docker compose up brings up 2 services (was 3)")
def test_docker_compose_up_brings_up_two_services():
    """Body populated by @scenario from the .feature file (skipped — Plan 2 Task 4)."""


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "future — Plan 2 Tasks 2 + 3; s6 service definitions + Dockerfile refactor. "
        "fix: land s6 services per Plan 2 Task 2 and the multi-stage Dockerfile per Plan 2 Task 3. "
        "next: drop the @pytest.mark.skip once the unified image builds. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "kairix container runs both api + worker via s6")
def test_kairix_container_runs_both_api_and_worker_via_s6():
    """Body populated by @scenario from the .feature file (skipped — Plan 2 Tasks 2 + 3)."""


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "future — Plan 2 Task 3; Dockerfile declares USER kairix uid 995. "
        "fix: land the Dockerfile refactor per Plan 2 Task 3 (groupadd 985 + useradd 995 + USER kairix). "
        "next: drop the @pytest.mark.skip once the unified image runs as uid 995. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "Container runs as the kairix user (uid 995)")
def test_container_runs_as_kairix_user_uid_995():
    """Body populated by @scenario from the .feature file (skipped — Plan 2 Task 3)."""


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "future — Plan 2 Tasks 3 + 5; ownership of bind-mounted volumes. "
        "fix: land Dockerfile USER kairix per Plan 2 Task 3 and integration test per Plan 2 Task 5. "
        "next: drop the @pytest.mark.skip once the unified image writes files as 995:985. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "Files written to the volume land as kairix:kairix on host")
def test_files_written_to_volume_land_as_kairix_on_host():
    """Body populated by @scenario from the .feature file (skipped — Plan 2 Tasks 3 + 5)."""


@pytest.mark.bdd
@pytest.mark.skip(
    reason=(
        "future — Plan 2 Tasks 2 + 5; s6 signal forwarding + graceful shutdown integration test. "
        "fix: land s6 finish scripts per Plan 2 Task 2 and the SIGTERM integration test per Plan 2 Task 5. "
        "next: drop the @pytest.mark.skip once docker stop drains both processes within 30s. "
        "run: bash scripts/safe-commit.sh after the body lands."
    )
)
@scenario(FEATURE, "SIGTERM to the container shuts both processes gracefully")
def test_sigterm_to_container_shuts_both_processes_gracefully():
    """Body populated by @scenario from the .feature file (skipped — Plan 2 Tasks 2 + 5)."""
