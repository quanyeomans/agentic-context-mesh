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
def test_agent_memory_subtree_is_writable() -> None:
    """The agent-memory subtree mounts read-write while sources stay read-only.

    ``kairix remember`` / the ``memory_write`` MCP tool write dated memory
    files under ``04-Agent-Knowledge/<agent>/``; with only the ``:ro``
    documents bind the write path fails with EROFS (live-verified on the
    2026-06-11 showcase deployment). The nested bind keeps every other
    document read-only.
    """
    volumes = [str(volume) for volume in _kairix_service().get("volumes", [])]
    assert "./documents:/data/documents:ro" in volumes
    assert "./documents/04-Agent-Knowledge:/data/documents/04-Agent-Knowledge" in volumes


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


# ---------------------------------------------------------------------------
# #449 — .env.example credential placeholders + warn-and-degrade messaging
# ---------------------------------------------------------------------------

_LLM_KEY_SENTINEL = "PASTE-YOUR-LLM-KEY-HERE"
_LLM_ENDPOINT_SENTINEL = "PASTE-YOUR-LLM-ENDPOINT-HERE"


def _env_example_lines(rel_path: str) -> dict[str, str]:
    """Parse a .env.example into a {KEY: VALUE} dict (comments ignored)."""
    text = (_REPO_ROOT / rel_path).read_text()
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", [".env.example", "docker/.env.example"])
def test_env_example_carries_paste_llm_sentinels(rel_path: str) -> None:
    """#449: both .env.example files ship obvious-broken PASTE-... LLM sentinels.

    A literal ``your-api-key-here`` reads truthy and silently degrades vector
    search; the ``PASTE-...`` sentinels are unmistakably placeholders so an
    operator (and the startup preflight) can detect "you forgot to set this".
    """
    env = _env_example_lines(rel_path)
    assert env.get("KAIRIX_PROVIDER_LLM_API_KEY") == _LLM_KEY_SENTINEL, rel_path
    assert env.get("KAIRIX_PROVIDER_LLM_ENDPOINT") == _LLM_ENDPOINT_SENTINEL, rel_path


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", [".env.example", "docker/.env.example"])
def test_env_example_has_top_of_file_placeholder_warning(rel_path: str) -> None:
    """#449: a top-of-file WARNING explains the PASTE-... values are placeholders.

    The message must say the container still boots (no crash-loop) and that
    vector search stays disabled until the values are replaced — the
    warn-and-degrade contract in plain operator English.
    """
    header = "\n".join((_REPO_ROOT / rel_path).read_text().splitlines()[:14])
    assert "WARNING" in header, rel_path
    assert "PASTE-" in header, rel_path
    assert "boot" in header.lower(), rel_path
    assert "vector" in header.lower(), rel_path


@pytest.mark.unit
@pytest.mark.parametrize("rel_path", [".env.example", "docker/.env.example"])
def test_env_example_neo4j_password_stays_non_empty(rel_path: str) -> None:
    """#449: the neo4j password sentinel must be non-empty — an empty value hard-fails boot."""
    env = _env_example_lines(rel_path)
    password = env.get("KAIRIX_NEO4J_PASSWORD", "")
    assert password, f"{rel_path}: KAIRIX_NEO4J_PASSWORD must be a non-empty sentinel"


# ---------------------------------------------------------------------------
# PLA-276 / #486 — docker-compose.example.yml (the shared-host / VM template)
# must mount the same FHS data + cache volumes and point KAIRIX_DOCUMENT_ROOT
# at the same bind target as the canonical docker-compose.yml. A stale
# ``kairix-data:/data/kairix`` mount (with no cache volume and no document-root
# override) left the SQLite index + caches on the container's EPHEMERAL
# writable layer and made ``kairix embed`` scan the empty baked document dir —
# a broken, non-persistent install for anyone following the example.
# ---------------------------------------------------------------------------

_FHS_DATA_MOUNT = "kairix-data:/var/lib/kairix"
_FHS_CACHE_MOUNT = "kairix-cache:/var/cache/kairix"


def _load_example_compose() -> dict:
    """Parse the repo-root docker-compose.example.yml."""
    parsed = yaml.safe_load((_REPO_ROOT / "docker-compose.example.yml").read_text())
    assert isinstance(parsed, dict), "docker-compose.example.yml must parse to a mapping"
    return parsed


def _service_volumes(service: dict) -> list[str]:
    return [str(volume) for volume in service.get("volumes", [])]


def _kairix_image_services(parsed: dict) -> dict[str, dict]:
    """Services running the kairix image (every one needs the FHS mounts +
    document root); skips the neo4j sidecar."""
    return {name: svc for name, svc in parsed["services"].items() if "three-cubes/kairix" in str(svc.get("image", ""))}


@pytest.mark.unit
def test_example_compose_document_root_matches_canonical() -> None:
    """Every kairix-image service in the example sets KAIRIX_DOCUMENT_ROOT to
    the same bind target as the canonical compose, so ``kairix embed`` scans
    the mounted docs rather than the empty baked dir (#486 / PLA-276)."""
    canonical_doc_root = _environment_as_dict(_kairix_service()).get("KAIRIX_DOCUMENT_ROOT")
    assert canonical_doc_root == "/data/documents", "canonical compose document-root drifted"
    example_services = _kairix_image_services(_load_example_compose())
    assert example_services, "example compose must declare at least one kairix-image service"
    for name, svc in example_services.items():
        assert _environment_as_dict(svc).get("KAIRIX_DOCUMENT_ROOT") == canonical_doc_root, name


@pytest.mark.unit
def test_example_compose_mounts_match_canonical_fhs_volumes() -> None:
    """Every kairix-image service in the example mounts the SAME persistent
    data + cache named volumes as the canonical compose
    (``kairix-data:/var/lib/kairix`` + ``kairix-cache:/var/cache/kairix``),
    and both volumes are declared. The retired ``kairix-data:/data/kairix``
    mount that put the index on the ephemeral layer is gone (#486 / #447)."""
    canonical_named = {
        volume for volume in _service_volumes(_kairix_service()) if volume.startswith(("kairix-data:", "kairix-cache:"))
    }
    assert canonical_named == {_FHS_DATA_MOUNT, _FHS_CACHE_MOUNT}, "canonical compose volumes drifted"

    example = _load_example_compose()
    for name, svc in _kairix_image_services(example).items():
        example_named = {
            volume for volume in _service_volumes(svc) if volume.startswith(("kairix-data:", "kairix-cache:"))
        }
        assert example_named == canonical_named, name
        assert "kairix-data:/data/kairix" not in _service_volumes(svc), name
    assert "kairix-cache" in (example.get("volumes") or {})
    assert "kairix-data" in (example.get("volumes") or {})
