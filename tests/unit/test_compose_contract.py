"""Contract tests for the published docker-compose.yml + s6 api run script.

Issues #470 / #471: the published compose file must start on a fresh
laptop/VM with nothing more than ``cp .env.example .env`` and
``cp kairix.config.example.yaml kairix.config.yaml``, and the
in-container MCP server must be reachable through the published port.

These tests pin the compose wiring as data (``yaml.safe_load``) so the
quick-start path can't silently regress:

  - the kairix service reads ``.env`` (not a VM-only ``/run/secrets`` file)
  - the container sees user documents at the path compose mounts them
  - the bundled neo4j sidecar is reachable by its compose DNS name
  - the host port is operator-tunable via ``KAIRIX_HOST_PORT``
  - the operator config file is bind-mounted into the container
  - the s6-supervised api process binds all container interfaces so the
    published port actually answers (#471)
"""

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_compose() -> dict:
    """Parse the repo-root docker-compose.yml."""
    parsed = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    assert isinstance(parsed, dict), "docker-compose.yml must parse to a mapping"
    return parsed


def _kairix_service() -> dict:
    """Return the kairix service block from the compose file."""
    service = _load_compose()["services"]["kairix"]
    assert isinstance(service, dict), "kairix service block must be a mapping"
    return service


def _environment_as_dict(service: dict) -> dict[str, str]:
    """Normalise compose ``environment:`` (mapping or KEY=VALUE list) to a dict."""
    env = service.get("environment", {})
    if isinstance(env, dict):
        return {str(key): str(value) for key, value in env.items()}
    entries = {}
    for item in env:
        key, _, value = str(item).partition("=")
        entries[key] = value
    return entries


@pytest.mark.unit
def test_kairix_service_reads_dot_env() -> None:
    """env_file is exactly [".env"] so a fresh host starts after ``cp .env.example .env``.

    The VM sidecar path (/run/secrets/kairix.env) lives in its own compose
    override, never in the base file — a missing sidecar file hard-fails
    ``docker compose up`` on every fresh machine (#470).
    """
    assert _kairix_service().get("env_file") == [".env"]


@pytest.mark.unit
def test_base_compose_never_wires_run_secrets() -> None:
    """No service in the base compose file mounts or reads /run/secrets/kairix.env (#470).

    Asserts on the parsed YAML (env_file + volumes per service) rather
    than raw text — the header comment legitimately mentions the path
    when pointing VM sidecar deploys at their own compose override.
    """
    for name, service in _load_compose()["services"].items():
        env_files = service.get("env_file", [])
        volumes = [str(volume) for volume in service.get("volumes", [])]
        assert "/run/secrets/kairix.env" not in [str(f) for f in env_files], name
        assert not any("/run/secrets/kairix.env" in volume for volume in volumes), name


@pytest.mark.unit
def test_kairix_service_points_document_root_at_the_mounted_folder() -> None:
    """KAIRIX_DOCUMENT_ROOT matches the ./documents bind target.

    The image bakes /var/lib/kairix/documents; without this override the
    mounted documents are invisible to ``kairix embed`` (#470).
    """
    env = _environment_as_dict(_kairix_service())
    assert env.get("KAIRIX_DOCUMENT_ROOT") == "/data/documents"


@pytest.mark.unit
def test_kairix_service_reaches_bundled_neo4j_by_service_name() -> None:
    """KAIRIX_NEO4J_URI targets the neo4j sidecar via compose DNS (#470)."""
    env = _environment_as_dict(_kairix_service())
    assert env.get("KAIRIX_NEO4J_URI") == "bolt://neo4j:7687"


@pytest.mark.unit
def test_kairix_service_host_port_is_operator_tunable() -> None:
    """The published port reads KAIRIX_HOST_PORT with an 8080 default.

    Docs say 8080 everywhere; .env.example documents KAIRIX_HOST_PORT —
    the compose file must actually read it (#470).
    """
    ports = [str(port) for port in _kairix_service().get("ports", [])]
    assert any("${KAIRIX_HOST_PORT:-8080}" in port for port in ports), ports


@pytest.mark.unit
def test_kairix_service_mounts_operator_config_file() -> None:
    """kairix.config.yaml is bind-mounted so operators can supply collections/agents (#470)."""
    volumes = [str(volume) for volume in _kairix_service().get("volumes", [])]
    assert "./kairix.config.yaml:/etc/kairix/kairix.config.yaml:ro" in volumes


@pytest.mark.unit
def test_api_run_script_binds_all_container_interfaces() -> None:
    """The s6 api service passes --host 0.0.0.0 to ``kairix mcp serve``.

    Without it the server binds 127.0.0.1 INSIDE the container and the
    published port refuses every connection from the host (#471). Host
    exposure stays governed by the compose port bind, which defaults to
    127.0.0.1 on the host side.
    """
    run_script = (_REPO_ROOT / "docker" / "s6" / "services" / "kairix-api" / "run").read_text()
    assert "--host 0.0.0.0" in run_script
