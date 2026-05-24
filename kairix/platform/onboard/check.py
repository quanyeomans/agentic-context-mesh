"""
kairix.platform.onboard.check — deployment health checks.

Each check is independent and returns a CheckResult with:
  name   — short identifier
  ok     — True if the check passed
  detail — human-readable explanation of status
  fix    — actionable remediation hint (None when ok=True)

run_all_checks() returns the full list. Checks are ordered from most-fundamental
(PATH, secrets) to most-dependent (vector search, entity graph) so failures are
diagnosed from the bottom up.

run_onboard_check() wraps run_all_checks() and returns a structured
OnboardResult — the canonical surface for ``kairix onboard check --json``
and for any caller (CI, docker-compose healthcheck, MCP probe) that needs
to act on individual failures programmatically.

Failure modes:
  - Checks never raise; exceptions are caught and surfaced as failed CheckResult.
  - Checks that require live external services (Neo4j, Azure KV) degrade gracefully.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairix.paths import mcp_port as _mcp_port

logger = logging.getLogger(__name__)

# F17 — check names appear as registry keys, CheckResult names emitted from each
# check, and remediation lookups; one constant per name keeps the check identifier
# in a single edit site.
_CHECK_QUERY_CACHE_STATS = "query_cache_stats"
_CHECK_EMBED_CACHE_STATS = "embed_cache_stats"
_CHECK_KAIRIX_ON_PATH = "kairix_on_path"
_CHECK_WRAPPER_INSTALLED = "wrapper_installed"
_CHECK_SECRETS_LOADED = "secrets_loaded"  # pragma: allowlist secret — check-name string, not a credential
_CHECK_DOCUMENT_ROOT_CONFIGURED = "document_root_configured"
_CHECK_VECTOR_SEARCH_WORKING = "vector_search_working"
_CHECK_NEO4J_REACHABLE = "neo4j_reachable"
_CHECK_AGENT_KNOWLEDGE_POPULATED = "agent_knowledge_populated"
_CHECK_CHUNK_DATE_POPULATED = "chunk_date_populated"
_CHECK_MCP_SERVICE = "mcp_service"
_CHECK_TOPOLOGY_V2_CONFIG_VALID = "topology_v2_config_valid"
_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED = "topology_v2_cc_pairs_registered"
_CHECK_SHAREPOINT_CREDENTIALS_LOADED = (
    "sharepoint_credentials_loaded"  # pragma: allowlist secret — check-name string, not a credential
)


@dataclass
class CheckResult:
    """Result of a single deployment check."""

    name: str
    ok: bool
    detail: str
    fix: str | None = field(default=None)


@dataclass(frozen=True)
class CheckFailure:
    """Single failed check, structured for machine consumption.

    Every failure carries:
      check       — short ID matching the underlying CheckResult.name
      detail      — one-line explanation of what's wrong
      remediation — exact operator-actionable command/check the operator
                    should run NOW (never empty)

    Design principle: the remediation string must hand the operator their
    next concrete step. "Run `<command>`" or "Check `<path>` exists" — not
    a description of the failure state.
    """

    check: str
    detail: str
    remediation: str


@dataclass(frozen=True)
class OnboardResult:
    """Structured result of a full onboard check run.

    Fields:
      passed       — number of checks that returned ok=True
      total        — total number of checks executed
      failures     — list of CheckFailure, one per failed check, in
                     dependency order (most fundamental first)
      fully_passed — True iff passed == total (derived)

    The CLI's ``--json`` flag emits this directly; the human-readable
    output renders the same data with icons + indented remediations.

    Exit-code semantics: 0 when fully_passed is True, 1 otherwise.
    """

    passed: int
    total: int
    failures: list[CheckFailure]
    fully_passed: bool


# ---------------------------------------------------------------------------
# Canonical remediations
# ---------------------------------------------------------------------------
# Each check has a canonical operator-actionable remediation string. When a
# check returns a CheckResult with fix=None (or a structurally empty fix),
# the canonical remediation is substituted so every CheckFailure surfaces a
# concrete next step. The per-check CheckResult.fix strings remain the
# detailed multi-line guidance for human readers; CheckFailure.remediation
# is the one-line "run this now" command an agent or healthcheck can act on.

_CANONICAL_REMEDIATIONS: dict[str, str] = {
    _CHECK_QUERY_CACHE_STATS: (
        "Diagnostic check — no remediation required. Cache hit-rate is informational; "
        "tune `KAIRIX_QUERY_CACHE_MAX_ENTRIES` / `KAIRIX_QUERY_CACHE_MAX_AGE_S` if needed."
    ),
    _CHECK_EMBED_CACHE_STATS: (
        "Diagnostic check — no remediation required. Cache hit-rate is informational; "
        "tune `KAIRIX_EMBED_CACHE_MAX_ENTRIES` / `KAIRIX_EMBED_CACHE_MAX_AGE_S` if needed."
    ),
    _CHECK_KAIRIX_ON_PATH: (
        "Run `bash scripts/deploy-vm.sh` on the host to install the wrapper + symlink; "
        "or manually export `PATH=/opt/openclaw/bin:$PATH`."
    ),
    _CHECK_WRAPPER_INSTALLED: (
        "Run `bash scripts/deploy-vm.sh` to install /opt/kairix/bin/kairix-wrapper.sh "
        "and repoint /usr/local/bin/kairix at it."
    ),
    _CHECK_SECRETS_LOADED: (
        "Run `sudo systemctl enable --now kairix-fetch-secrets.service` on the host; "
        "if that fails, confirm `/run/secrets/kairix.env` exists and contains "
        "`KAIRIX_LLM_API_KEY=...` and `KAIRIX_LLM_ENDPOINT=...`."
    ),
    _CHECK_DOCUMENT_ROOT_CONFIGURED: (
        "Set `KAIRIX_DOCUMENT_ROOT=/your/docs/path` in /opt/kairix/service.env and ensure the directory exists."
    ),
    _CHECK_VECTOR_SEARCH_WORKING: (
        "Run `docker logs kairix-worker-1` for embed-pipeline errors; confirm "
        "`kairix onboard check secrets_loaded` passes; then run `kairix embed --limit 20` "
        "to test the embed pipeline."
    ),
    _CHECK_NEO4J_REACHABLE: (
        "Run `bash scripts/install-neo4j.sh` to install Neo4j, then set "
        "`KAIRIX_NEO4J_URI=bolt://localhost:7687` in /opt/kairix/service.env. "
        "Neo4j is optional — entity boost degrades gracefully when offline."
    ),
    _CHECK_AGENT_KNOWLEDGE_POPULATED: (
        "Run `kairix embed` to populate the agent knowledge store from the document root, "
        "or create at least one memory file at "
        "$KAIRIX_DOCUMENT_ROOT/04-Agent-Knowledge/<agent>/memory/YYYY-MM-DD.md."
    ),
    _CHECK_CHUNK_DATE_POPULATED: (
        "Run `kairix embed --rebuild-canaries` to refresh the chunk_date index. "
        "If chunk_date is missing entirely, run `kairix embed` to trigger the migration."
    ),
    _CHECK_MCP_SERVICE: (
        "Register kairix with at least one MCP consumer harness: "
        '`openclaw mcp set mcp-kairix \'{"type":"stdio","command":"/path/to/kairix-start.sh"}\'`, '
        "add to ~/Library/Application Support/Claude/claude_desktop_config.json, or run "
        "`sudo systemctl enable --now kairix-mcp.service`."
    ),
    _CHECK_TOPOLOGY_V2_CONFIG_VALID: (
        "fix: open kairix.config.yaml and resolve the topology_v2 cross-reference "
        "failures (every cc_pair must reference a declared connector + credential; "
        "every collection source / scope_profile entry / skill source must reference "
        "a declared cc_pair / collection). next: run `kairix config validate` to "
        "re-run the validator."
    ),
    _CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED: (
        "fix: restart the worker (e.g. `docker compose restart kairix-worker` or "
        "`systemctl restart kairix-worker`) — the apply-bridge runs at boot when "
        "`topology_v2_config` is on and materialises declared cc_pairs idempotently. "
        "next: re-run `kairix onboard check` to confirm every declared cc_pair has a row."
    ),
    _CHECK_SHAREPOINT_CREDENTIALS_LOADED: (
        "fix: set the three M365 client-credentials secrets that the SharePoint "
        "connector resolves via kairix.secrets.get_secret — "
        "`connector-m365-tenant-id`, `connector-m365-client-id`, and "
        "`connector-m365-client-secret`. Docker operators write them as "
        "KEY=VALUE lines in /run/secrets/kairix.env (uppercase + dash → underscore: "
        "CONNECTOR_M365_TENANT_ID etc.); pip operators set them in "
        "~/.kairix/secrets.env or as plain env vars. next: re-run "
        "`kairix onboard check sharepoint_credentials_loaded`."
    ),
}

_UNKNOWN_CHECK_REMEDIATION = (
    "Report this failure as a bug in kairix.platform.onboard.check — the check has no canonical remediation registered."
)


def _remediation_for(check_name: str, fix: str | None) -> str:
    """Return the canonical remediation for *check_name*.

    Falls back to the per-check ``fix`` string only when the check name has
    no canonical entry (which should never happen for production checks —
    the dict above is the source of truth). Never returns empty.
    """
    canonical = _CANONICAL_REMEDIATIONS.get(check_name)
    if canonical:
        return canonical
    if fix and fix.strip():
        return fix.strip()
    return _UNKNOWN_CHECK_REMEDIATION


def _default_is_docker() -> bool:
    """Production ``is_docker`` — defers to ``kairix.paths.is_docker_runtime_check``."""
    from kairix.paths import is_docker_runtime_check as _impl

    return _impl()


@dataclass
class OnboardChecksDeps:
    """Injectable dependencies for the onboard health checks.

    Each field defaults to a production implementation; tests construct
    ``OnboardChecksDeps(which=fake_which, is_docker=lambda: True)``
    rather than threading per-check ``*_fn=None`` substitution kwargs
    through the public health-check signatures.
    """

    which: Callable[[str], str | None] = field(default_factory=lambda: shutil.which)
    is_docker: Callable[[], bool] = field(default_factory=lambda: _default_is_docker)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_kairix_on_path(deps: OnboardChecksDeps | None = None) -> CheckResult:
    """kairix is findable via PATH.

    ``deps.which`` is the DI seam (defaults to ``shutil.which``); tests
    pass a ``OnboardChecksDeps`` with a callable returning the desired
    result so the live PATH never needs mutating.
    """
    d = deps if deps is not None else OnboardChecksDeps()
    path = d.which("kairix")
    if path is None:
        return CheckResult(
            name=_CHECK_KAIRIX_ON_PATH,
            ok=False,
            detail="kairix not found on PATH",
            fix=(
                "Add the kairix symlink directory to PATH.\n"
                "Run: bash scripts/deploy-vm.sh  (sets up /etc/profile.d/kairix.sh)\n"
                "Or manually: export PATH=/opt/openclaw/bin:$PATH"
            ),
        )
    return CheckResult(name=_CHECK_KAIRIX_ON_PATH, ok=True, detail=f"kairix found at {path}")


def check_wrapper_installed(deps: OnboardChecksDeps | None = None) -> CheckResult:
    """The kairix symlink points to a shell wrapper, not the raw Python binary.

    ``deps.is_docker`` and ``deps.which`` are the DI seams; production
    callers leave ``deps=None`` and defaults wire to ``kairix.paths.is_docker_runtime_check``
    and ``shutil.which``.
    """
    d = deps if deps is not None else OnboardChecksDeps()

    if d.is_docker():
        return CheckResult(
            name=_CHECK_WRAPPER_INSTALLED,
            ok=True,
            detail="Running in Docker — wrapper check skipped (pip install in image)",
        )

    path = d.which("kairix")
    if path is None:
        return CheckResult(
            name=_CHECK_WRAPPER_INSTALLED,
            ok=False,
            detail="kairix not on PATH — cannot check wrapper",
            fix="Run scripts/deploy-vm.sh to install the wrapper and symlink.",
        )

    resolved = Path(path).resolve()

    # Check if the binary is a shell script (starts with shebang that isn't python)
    try:
        with open(resolved, "rb") as f:
            header = f.read(128)
        first_line = header.split(b"\n")[0].decode("utf-8", errors="replace").strip()

        if first_line.startswith("#!") and "python" in first_line:
            return CheckResult(
                name=_CHECK_WRAPPER_INSTALLED,
                ok=False,
                detail=f"kairix symlink points to raw Python binary: {resolved}",
                fix=(
                    "The symlink should point to kairix-wrapper.sh, not the Python binary.\n"
                    "Run the deploy script to fix:\n"
                    "  bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/kairix/main/scripts/deploy-vm.sh)\n"
                    "This installs the wrapper at /opt/kairix/bin/kairix-wrapper.sh\n"
                    "and updates /usr/local/bin/kairix to point to it."
                ),
            )
        if first_line.startswith("#!") and ("bash" in first_line or "sh" in first_line):
            return CheckResult(
                name=_CHECK_WRAPPER_INSTALLED,
                ok=True,
                detail=f"wrapper installed at {resolved}",
            )

        return CheckResult(
            name=_CHECK_WRAPPER_INSTALLED,
            ok=False,
            detail=f"kairix binary has unexpected format (header: {first_line[:60]})",
            fix="Run scripts/deploy-vm.sh to reinstall the wrapper.",
        )
    except Exception as exc:
        return CheckResult(
            name=_CHECK_WRAPPER_INSTALLED,
            ok=False,
            detail=f"Cannot read kairix binary at {resolved}: {exc}",
            fix="Check file permissions on the kairix binary.",
        )


_REQUIRED_SECRETS = ("KAIRIX_LLM_API_KEY", "KAIRIX_LLM_ENDPOINT")
_SECRETS_FILE_PROBE_PATHS = (
    "/run/secrets/kairix.env",
    "/opt/kairix/secrets.env",
)


def _secrets_file_keys_present(path: Path, keys: tuple[str, ...]) -> set[str]:
    """Return the subset of *keys* found as KEY= entries in a secrets file."""
    found: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k = line.split("=", 1)[0].strip()
            if k in keys:
                found.add(k)
    except OSError:
        pass
    return found


def check_secrets_loaded(env: Mapping[str, str] | None = None) -> CheckResult:
    """LLM credentials are available in the environment or a secrets file.

    ``env`` is a DI seam (defaults to ``os.environ``); tests pass an
    explicit mapping rather than monkeypatching the process environment.
    """
    if env is None:
        env = os.environ
    api_key = env.get("KAIRIX_LLM_API_KEY", "")
    endpoint = env.get("KAIRIX_LLM_ENDPOINT", "")

    # Tier 1 — credentials in process environment (wrapper loaded them)
    if api_key and endpoint:
        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
        return CheckResult(
            name=_CHECK_SECRETS_LOADED,
            ok=True,
            detail=f"LLM credentials present (key: {masked_key}, endpoint: {endpoint[:40]}...)",
        )

    # Tier 2 — probe secrets file directly (credentials present but not yet in env;
    # load_secrets() is called lazily on first provider plugin construction)
    secrets_file_env = env.get("KAIRIX_SECRETS_FILE", "")
    probe_paths: tuple[str, ...] = (
        (secrets_file_env, *_SECRETS_FILE_PROBE_PATHS) if secrets_file_env else _SECRETS_FILE_PROBE_PATHS
    )
    for probe in probe_paths:
        p = Path(probe)
        if not p.exists():
            continue
        found = _secrets_file_keys_present(p, _REQUIRED_SECRETS)
        missing_in_file = [k for k in _REQUIRED_SECRETS if k not in found]
        if not missing_in_file:
            return CheckResult(
                name=_CHECK_SECRETS_LOADED,
                ok=True,
                detail=(
                    f"Secrets file found at {probe} — credentials will be active on first search call. "
                    f"Run `kairix search` to confirm."
                ),
            )
        # File exists but is missing keys — give specific guidance
        return CheckResult(
            name=_CHECK_SECRETS_LOADED,
            ok=False,
            detail=f"Secrets file at {probe} is missing required keys: {', '.join(missing_in_file)}",
            fix=(
                f"Add the missing keys to {probe}:\n"
                + "".join(f"  {k}=<value>\n" for k in missing_in_file)
                + "Set KAIRIX_LLM_API_KEY and KAIRIX_LLM_ENDPOINT in your env or secrets file."
            ),
        )

    # Tier 3 — nothing found
    missing_env = [k for k in _REQUIRED_SECRETS if not os.environ.get(k)]
    default_path = _SECRETS_FILE_PROBE_PATHS[-1]
    return CheckResult(
        name=_CHECK_SECRETS_LOADED,
        ok=False,
        detail=f"LLM credentials not found in environment or secrets file: {', '.join(missing_env)}",
        fix=(
            f"Create {default_path} with:\n"
            "  KAIRIX_LLM_API_KEY=<value>\n"
            "  KAIRIX_LLM_ENDPOINT=<value>\n"
            "Or ensure the kairix wrapper (not the raw Python binary) is on PATH.\n"
            "Verify: head -1 $(which kairix)  — should show #!/usr/bin/env bash"
        ),
    )


def check_document_root_configured(env: Mapping[str, str] | None = None) -> CheckResult:
    """KAIRIX_DOCUMENT_ROOT is set and the directory exists.

    ``env`` is a DI seam (defaults to ``os.environ``); tests pass an
    explicit mapping rather than monkeypatching the process environment.
    """
    if env is None:
        env = os.environ
    doc_root = env.get("KAIRIX_DOCUMENT_ROOT", "")
    if not doc_root:
        return CheckResult(
            name=_CHECK_DOCUMENT_ROOT_CONFIGURED,
            ok=False,
            detail="KAIRIX_DOCUMENT_ROOT is not set",
            fix=("Set KAIRIX_DOCUMENT_ROOT in /opt/kairix/service.env:\n  KAIRIX_DOCUMENT_ROOT=/data/documents"),
        )
    p = Path(doc_root)
    if not p.exists():
        return CheckResult(
            name=_CHECK_DOCUMENT_ROOT_CONFIGURED,
            ok=False,
            detail=f"KAIRIX_DOCUMENT_ROOT directory does not exist: {doc_root}",
            fix=(
                "Create the directory or update KAIRIX_DOCUMENT_ROOT in /opt/kairix/service.env.\n"
                "If your documents are at a different path, set: KAIRIX_DOCUMENT_ROOT=/your/docs/path"
            ),
        )
    md_count = sum(1 for _ in p.rglob("*.md") if not _.name.startswith("."))
    return CheckResult(
        name=_CHECK_DOCUMENT_ROOT_CONFIGURED,
        ok=True,
        detail=f"Document root: {doc_root} ({md_count:,} .md files found)",
    )


# Backwards-compat alias


def check_vector_search_working(pipeline: Any | None = None) -> CheckResult:
    """Vector search returns results with vec_count > 0 (not BM25-only fallback).

    Args:
        pipeline: Injectable search pipeline for testing. Defaults to the
                  production ``build_search_pipeline()``.
    """
    try:
        if pipeline is None:
            from kairix.core.factory import build_search_pipeline

            pipeline = build_search_pipeline()
        result = pipeline.search(query="knowledge management", budget=500)

        vec_count = getattr(result, "vec_count", None)
        bm25_count = getattr(result, "bm25_count", None)
        vec_failed = getattr(result, "vec_failed", None)
        result_count = len(result.results) if hasattr(result, "results") else 0

        if vec_failed:
            return CheckResult(
                name=_CHECK_VECTOR_SEARCH_WORKING,
                ok=False,
                detail=(
                    f"Vector search failed (vec_failed=True). "
                    f"Results: {result_count} (BM25 only). bm25={bm25_count}, vec=0"
                ),
                fix=(
                    "Vector search failure usually means Azure credentials aren't loaded.\n"
                    "Check: kairix onboard check  — look at secrets_loaded result.\n"
                    "If secrets are loaded, check the embed ran:\n"
                    "  kairix search 'test query'\n"
                    "  If vec=0: run kairix embed --limit 20 to test."
                ),
            )

        if vec_count is not None and vec_count == 0 and result_count == 0:
            return CheckResult(
                name=_CHECK_VECTOR_SEARCH_WORKING,
                ok=False,
                detail="Search returned 0 results (vec=0, bm25=0) — vault may not be embedded yet",
                fix=(
                    "Run: kairix embed --limit 20  (test embed)\n"
                    "Then: kairix embed             (full vault embed)\n"
                    "See OPERATIONS.md §First-Run Sequence for full steps."
                ),
            )

        detail_parts = [f"results={result_count}"]
        if vec_count is not None:
            detail_parts.append(f"vec={vec_count}")
        if bm25_count is not None:
            detail_parts.append(f"bm25={bm25_count}")

        return CheckResult(
            name=_CHECK_VECTOR_SEARCH_WORKING,
            ok=True,
            detail=f"Vector search working ({', '.join(detail_parts)})",
        )

    except Exception as exc:
        return CheckResult(
            name=_CHECK_VECTOR_SEARCH_WORKING,
            ok=False,
            detail=f"Search raised an exception: {exc}",
            fix=(
                "Check KAIRIX_LLM_API_KEY and KAIRIX_LLM_ENDPOINT are set.\n"
                "Run: kairix onboard check  to see secrets_loaded status."
            ),
        )


def check_neo4j_reachable(neo4j_client: Any | None = None) -> CheckResult:
    """Neo4j is reachable and contains entities.

    Args:
        neo4j_client: Injectable Neo4j client for testing.
                      Defaults to the production client.
    """
    try:
        if neo4j_client is not None:
            client = neo4j_client
        else:
            from kairix.knowledge.graph.client import get_client

            client = get_client()
        if not getattr(client, "available", False):
            return CheckResult(
                name=_CHECK_NEO4J_REACHABLE,
                ok=False,
                detail="Neo4j client unavailable (KAIRIX_NEO4J_URI not set or connection refused)",
                fix=(
                    "Install Neo4j:\n"
                    "  bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/kairix/main/scripts/install-neo4j.sh)\n"
                    "Or run with Docker:\n"
                    "  docker run -d --name neo4j -p 7687:7687 -e NEO4J_AUTH=neo4j/YOUR_PASSWORD neo4j:5-community\n"
                    "Then set KAIRIX_NEO4J_URI in /opt/kairix/service.env:\n"
                    "  KAIRIX_NEO4J_URI=bolt://localhost:7687\n"
                    "Neo4j is optional — entity boost and multi-hop queries are degraded without it."
                ),
            )

        rows = client.cypher("MATCH (n) RETURN count(n) AS total LIMIT 1")
        total = rows[0]["total"] if rows else 0

        if total == 0:
            return CheckResult(
                name=_CHECK_NEO4J_REACHABLE,
                ok=False,
                detail="Neo4j reachable but empty — document crawler has not run",
                fix=(
                    "Populate the entity graph:\n"
                    "  kairix store crawl --document-root $KAIRIX_DOCUMENT_ROOT\n"
                    "Expected: ≥ 50 nodes for a typical document store."
                ),
            )

        return CheckResult(
            name=_CHECK_NEO4J_REACHABLE,
            ok=True,
            detail=f"Neo4j reachable — {total:,} nodes in graph",
        )

    except Exception as exc:
        return CheckResult(
            name=_CHECK_NEO4J_REACHABLE,
            ok=False,
            detail=f"Neo4j check failed: {exc}",
            fix=(
                "Verify Neo4j connection details in /opt/kairix/service.env.\n"
                "kairix degrades gracefully when Neo4j is unavailable — "
                "entity boost and multi-hop are disabled but search still works."
            ),
        )


def check_agent_knowledge_populated(document_root_path: Path | None = None) -> CheckResult:
    """At least one agent has memory logs (required for briefing pipeline).

    Args:
        document_root_path: Override for the document root. Defaults to
                            ``kairix.paths.document_root()``.
    """
    if document_root_path is None:
        from kairix.paths import document_root

        document_root_path = document_root()

    agent_knowledge = document_root_path / "04-Agent-Knowledge"
    if not agent_knowledge.exists():
        return CheckResult(
            name=_CHECK_AGENT_KNOWLEDGE_POPULATED,
            ok=False,
            detail=f"Agent knowledge directory not found: {agent_knowledge}",
            fix=(
                f"Create the directory:\n"
                f"  mkdir -p {agent_knowledge}/<agent-name>/memory\n"
                f"Agents write daily memory logs here during sessions."
            ),
        )

    # Look for any memory log files
    memory_files = list(agent_knowledge.rglob("*/memory/*.md"))
    if not memory_files:
        return CheckResult(
            name=_CHECK_AGENT_KNOWLEDGE_POPULATED,
            ok=False,
            detail=f"No agent memory logs found under {agent_knowledge}",
            fix=(
                "Agent memory logs are written by agents during sessions.\n"
                f"Expected path: {agent_knowledge}/<agent>/memory/YYYY-MM-DD.md\n"
                "Briefing synthesis (kairix brief) requires at least some memory content."
            ),
        )

    return CheckResult(
        name=_CHECK_AGENT_KNOWLEDGE_POPULATED,
        ok=True,
        detail=f"Agent memory logs found: {len(memory_files)} files under {agent_knowledge}",
    )


def _query_chunk_date_counts(db: Any) -> tuple[int, int] | CheckResult:
    """Inspect content_vectors for chunk_date coverage. Returns (total, dated) or an error CheckResult.

    Extracted from ``check_chunk_date_populated`` so the column-missing
    early-return doesn't have to live inside the outer try/except — that
    nesting pushed the parent over F16's ceiling.
    """
    cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
    if "chunk_date" not in cols:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail="chunk_date column missing from content_vectors",
            fix=(
                "Run kairix embed to add the column and populate dates.\nThe migration is automatic on next embed run."
            ),
        )
    total = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
    dated = db.execute("SELECT COUNT(*) FROM content_vectors WHERE chunk_date IS NOT NULL").fetchone()[0]
    return total, dated


def _grade_chunk_date_coverage(total: int, dated: int) -> CheckResult:
    """Turn raw chunk_date counts into a CheckResult with the right severity.

    Extracted from ``check_chunk_date_populated`` to flatten the if/if/if
    severity-ladder out of the outer try-block. Same F16 motivation as
    ``_query_chunk_date_counts``.
    """
    if total == 0:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail="content_vectors is empty — vault has not been embedded",
            fix="Run: kairix embed",
        )
    pct = 100 * dated / total
    if dated == 0:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail=f"chunk_date: 0/{total} chunks dated (0%) — TMP-7B temporal boost is inert",
            fix=(
                "Run kairix embed to populate chunk_date from document frontmatter and filenames.\n"
                "Documents need 'date: YYYY-MM-DD' in frontmatter or a date in their filename."
            ),
        )
    if pct < 20:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail=f"chunk_date: {dated}/{total} chunks dated ({pct:.0f}%) — low coverage, temporal boost degraded",
            fix=(
                "Add 'date: YYYY-MM-DD' frontmatter to more documents, or use dated filenames.\n"
                "Re-run kairix embed after updating documents."
            ),
        )
    return CheckResult(
        name=_CHECK_CHUNK_DATE_POPULATED,
        ok=True,
        detail=f"chunk_date: {dated}/{total} chunks dated ({pct:.0f}%)",
    )


def check_chunk_date_populated(
    db_path: Path | None = None,
    *,
    opener: Callable[[Path | None], Any] | None = None,
) -> CheckResult:
    """chunk_date is populated in content_vectors (required for TMP-7B temporal boost).

    Args:
        db_path: Override for the SQLite DB path. Defaults to
                 ``kairix.core.db.get_db_path()``.
        opener:  Public DI seam — when ``None`` the production
                 ``kairix.core.db.open_db`` is used; tests pass a raising
                 fake to drive the FileNotFoundError / generic-exception
                 branches without monkey-patching ``open_db``.
    """
    try:
        _opener: Callable[[Path | None], Any]
        if opener is None:
            from kairix.core.db import open_db

            _opener = open_db
        else:
            _opener = opener

        if db_path is None:
            from kairix.core.db import get_db_path

            db_path = Path(get_db_path())

        db = _opener(db_path)
        try:
            counts_or_error = _query_chunk_date_counts(db)
        finally:
            db.close()

        if isinstance(counts_or_error, CheckResult):
            return counts_or_error
        total, dated = counts_or_error
        return _grade_chunk_date_coverage(total, dated)

    except FileNotFoundError:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail="Index not found — vault not embedded yet",
            fix="Run: kairix embed",
        )
    except Exception as exc:
        return CheckResult(
            name=_CHECK_CHUNK_DATE_POPULATED,
            ok=False,
            detail=f"chunk_date check failed: {exc}",
            fix="Check kairix index at ~/.cache/kairix/index.sqlite",
        )


# ---------------------------------------------------------------------------
# MCP consumer harness checks
# ---------------------------------------------------------------------------
# kairix MCP server is a general service. Different consumers connect via
# different transports:
#
#   stdio (per-session subprocess) — OpenClaw, Claude Desktop, any orchestrator
#   SSE / HTTP (persistent process) — curl, generic HTTP MCP clients
#
# The check below probes whichever harnesses are detectable on this host.
# It passes if at least one harness is configured and functional.
# ---------------------------------------------------------------------------

_MCP_KAIRIX_SERVER_NAME = "mcp-kairix"

# ── OpenClaw ──────────────────────────────────────────────────────────────────
_OPENCLAW_JSON_PATHS = (
    str(Path.home() / ".openclaw" / "openclaw.json"),
    Path.home() / ".openclaw" / "openclaw.json",
)

# ── Claude Desktop ────────────────────────────────────────────────────────────
_CLAUDE_DESKTOP_CONFIG_PATHS = (
    # macOS
    Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
    # Linux (XDG)
    Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "Claude" / "claude_desktop_config.json",
)

# ── SSE / HTTP ────────────────────────────────────────────────────────────────
# Env read lives in kairix.paths.mcp_port (F4 — env reads stay in paths/secrets).
_MCP_SSE_PORT = _mcp_port()


def _check_openclaw_config_file(p: Path) -> tuple[bool, str] | None:
    """Inspect one OpenClaw config file for a kairix MCP registration.

    Returns a (ok, detail) verdict when the file registers kairix,
    otherwise ``None`` so the caller continues to the next candidate.
    Extracted from ``_probe_openclaw_harness`` so the per-candidate
    parse + dispatch chain doesn't have to live inside the outer
    for-loop's try/except — that nesting pushed the parent over F16.
    """
    import json as _json

    try:
        data = _json.loads(p.read_text())
    except (OSError, _json.JSONDecodeError):
        return None
    # OpenClaw stores MCP servers at mcp.servers (set via `openclaw mcp set`)
    mcp_servers = data.get("mcp", {}).get("servers", {})
    if _MCP_KAIRIX_SERVER_NAME not in mcp_servers:
        return None
    entry = mcp_servers[_MCP_KAIRIX_SERVER_NAME]
    cmd = entry.get("command", "")
    cmd_ok = bool(cmd) and Path(cmd).exists() and os.access(cmd, os.X_OK)
    if cmd_ok:
        return True, f"OpenClaw: registered in {p.name}, start command executable"
    return False, f"OpenClaw: registered but start command missing/not executable: {cmd}"


def _probe_openclaw_harness(*, config_paths: tuple[Path | str, ...] | None = None) -> tuple[bool, str]:
    """Return (ok, detail) for the OpenClaw stdio harness.

    ``config_paths`` is the public seam — production callers leave it
    ``None`` and the function uses module-level ``_OPENCLAW_JSON_PATHS``;
    tests pass a tmp-path tuple to drive the registered / missing /
    bad-command branches without monkey-patching the constant.
    """
    paths = config_paths if config_paths is not None else _OPENCLAW_JSON_PATHS
    for candidate in paths:
        p = Path(str(candidate))
        if not p.exists():
            continue
        verdict = _check_openclaw_config_file(p)
        if verdict is not None:
            return verdict

    # Fallback: try openclaw CLI
    try:
        # safe: subprocess with trusted system binary (openclaw)
        result = subprocess.run(
            [
                "openclaw",
                "mcp",
                "list",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if _MCP_KAIRIX_SERVER_NAME in result.stdout:
            return True, "OpenClaw: registered (via 'openclaw mcp list')"
    except Exception:  # noqa: S110 — expected when openclaw not installed
        pass

    return False, "OpenClaw: not detected"


def _probe_claude_desktop_harness(*, config_paths: tuple[Path, ...] | None = None) -> tuple[bool, str]:
    """Return (ok, detail) for the Claude Desktop stdio harness.

    ``config_paths`` is the public seam — production callers leave it
    ``None`` and the function uses module-level ``_CLAUDE_DESKTOP_CONFIG_PATHS``.
    """
    import json as _json

    paths = config_paths if config_paths is not None else _CLAUDE_DESKTOP_CONFIG_PATHS
    for candidate in paths:
        try:
            p = Path(str(candidate))
            if not p.exists():
                continue
            data = _json.loads(p.read_text())
            mcp_servers = data.get("mcpServers", {})
            if "kairix" in mcp_servers:
                entry = mcp_servers["kairix"]
                cmd = entry.get("command", "")
                return True, f"Claude Desktop: registered (command: {cmd})"
        except (OSError, _json.JSONDecodeError):
            continue

    return False, "Claude Desktop: not detected"


def _probe_sse_harness() -> tuple[bool, str]:
    """Return (ok, detail) for the SSE/HTTP persistent service harness."""
    import socket

    # TCP probe on MCP SSE port
    try:
        with socket.create_connection(("127.0.0.1", _MCP_SSE_PORT), timeout=2):
            return True, f"SSE/HTTP: listening on port {_MCP_SSE_PORT}"
    except OSError:
        pass

    # Fallback: check systemd unit exists and is active
    try:
        # safe: subprocess with trusted system binary (systemctl)
        result = subprocess.run(
            [
                "systemctl",
                "is-active",
                "kairix-mcp.service",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = result.stdout.strip()
        if state == "active":
            return (
                True,
                f"SSE/HTTP: kairix-mcp.service active (port {_MCP_SSE_PORT} not yet listening — may still be starting)",
            )
        elif state not in ("", "inactive", "failed", "unknown"):
            return False, f"SSE/HTTP: kairix-mcp.service state={state}"
    except Exception:  # noqa: S110 — expected when systemctl not available
        pass

    return False, f"SSE/HTTP: not listening on port {_MCP_SSE_PORT}"


def check_mcp_service(
    *,
    openclaw_probe: Callable[..., tuple[bool, str]] | None = None,
    claude_desktop_probe: Callable[..., tuple[bool, str]] | None = None,
    sse_probe: Callable[..., tuple[bool, str]] | None = None,
) -> CheckResult:
    """
    kairix MCP server is reachable by at least one configured consumer.

    Probes each transport harness that is detectable on this host:
      - OpenClaw (stdio): mcp-kairix registered in openclaw.json
      - Claude Desktop (stdio): kairix registered in claude_desktop_config.json
      - SSE/HTTP (persistent): port 7443 listening or kairix-mcp.service active

    Passes if at least one harness is configured and functional.
    If no harness is detected, reports which harnesses are available to configure.

    The three ``*_probe`` kwargs are the public DI seams — production
    callers leave them ``None`` and the function uses the module-level
    ``_probe_*_harness`` defaults; tests inject stubs to drive each
    harness's outcome without monkey-patching the module attributes.
    """
    openclaw_ok, openclaw_detail = (openclaw_probe or _probe_openclaw_harness)()
    claude_ok, claude_detail = (claude_desktop_probe or _probe_claude_desktop_harness)()
    sse_ok, sse_detail = (sse_probe or _probe_sse_harness)()

    active = [
        d
        for ok, d in [
            (openclaw_ok, openclaw_detail),
            (claude_ok, claude_detail),
            (sse_ok, sse_detail),
        ]
        if ok
    ]
    inactive = [
        d
        for ok, d in [
            (openclaw_ok, openclaw_detail),
            (claude_ok, claude_detail),
            (sse_ok, sse_detail),
        ]
        if not ok
    ]

    if active:
        return CheckResult(
            name=_CHECK_MCP_SERVICE,
            ok=True,
            detail="kairix MCP server accessible — " + "; ".join(active),
        )

    return CheckResult(
        name=_CHECK_MCP_SERVICE,
        ok=False,
        detail="kairix MCP server not configured for any consumer — " + "; ".join(inactive),
        fix=(
            "Configure at least one MCP consumer harness:\n\n"
            "  OpenClaw (stdio):\n"
            "    openclaw mcp set mcp-kairix "
            '\'{"type":"stdio","command":"/path/to/kairix-start.sh"}\'\n\n'
            "  Claude Desktop (stdio): add to ~/Library/Application Support/Claude/claude_desktop_config.json:\n"
            '    {"mcpServers": {"kairix": {"command": "kairix", "args": ["mcp", "serve"]}}}\n\n'
            "  SSE/HTTP (persistent service):\n"
            "    sudo systemctl enable --now kairix-mcp.service\n"
            "    # or: kairix mcp serve --transport sse --port 7443 &\n"
        ),
    )


# ---------------------------------------------------------------------------
# Run all checks
# ---------------------------------------------------------------------------


def check_query_cache_stats(query_cache: Any | None = None) -> CheckResult:
    """Diagnostic: report query-result cache stats (#281).

    Always passes — the existence of this check is so operators can
    see the cache hit-rate in ``kairix onboard check --json``. A
    process that has run zero queries reports size=0, hit_rate=0.0
    and still passes.

    Args:
        query_cache: Override for the process-shared
            :class:`QueryResultCache`. Tests pass an explicit instance
            rather than relying on the lazy module-level singleton.
    """
    try:
        if query_cache is None:
            from kairix.core.factory import get_query_cache

            query_cache = get_query_cache()
        stats = query_cache.stats()
        detail = (
            f"query cache: size={stats.size}, hits={stats.hits}, "
            f"misses={stats.misses}, hit_rate={stats.hit_rate:.2f}, "
            f"oldest_age_s={stats.oldest_entry_age_s:.1f}, "
            f"evictions={stats.evictions}"
        )
        return CheckResult(name=_CHECK_QUERY_CACHE_STATS, ok=True, detail=detail)
    except Exception as exc:
        # Diagnostic check must never block onboarding; a missing cache
        # is reported as a passing check with a degraded detail string
        # rather than a failure (operators see the warning, not a red).
        return CheckResult(
            name=_CHECK_QUERY_CACHE_STATS,
            ok=True,
            detail=f"query cache: unavailable ({exc})",
        )


def check_embed_cache_stats(embed_cache: Any | None = None) -> CheckResult:
    """Diagnostic: report embed-cache stats.

    The embed cache (``kairix.transport.cache``) sits in front
    of the Azure embed roundtrip — same text → same vector regardless
    of which agent / scope asked. This check exists so operators can
    see hit-rate / size in ``kairix onboard check --json`` alongside
    the result-cache (#281) stats. A process that has run zero embeds
    reports size=0, hit_rate=0.0 and still passes.

    Args:
        embed_cache: Override for the process-shared
            :class:`EmbedCache`. Tests pass an explicit instance
            rather than relying on the lazy module-level singleton.
    """
    try:
        if embed_cache is None:
            from kairix.transport.cache import get_embed_cache

            embed_cache = get_embed_cache()
        stats = embed_cache.stats()
        detail = (
            f"embed cache: size={stats.size}, hits={stats.hits}, "
            f"misses={stats.misses}, hit_rate={stats.hit_rate:.2f}, "
            f"oldest_age_s={stats.oldest_entry_age_s:.1f}, "
            f"evictions={stats.evictions}"
        )
        return CheckResult(name=_CHECK_EMBED_CACHE_STATS, ok=True, detail=detail)
    except Exception as exc:
        # Diagnostic check must never block onboarding; a missing cache
        # is reported as a passing check with a degraded detail string
        # rather than a failure (operators see the warning, not a red).
        return CheckResult(
            name=_CHECK_EMBED_CACHE_STATS,
            ok=True,
            detail=f"embed cache: unavailable ({exc})",
        )


# ---------------------------------------------------------------------------
# v2026.5.24a1 — topology v2 + SharePoint credential checks
# ---------------------------------------------------------------------------
# Each check below is gated by a feature flag — when the flag is OFF the
# check returns ok=True with a "skipped (flag off)" detail so a fresh
# deployment sees the check exists but is inert. Once the operator flips
# the corresponding flag, the check exercises the live config / DB /
# secrets path.
#
# Default flag values are read through the standard resolver
# (``kairix.core.features.flag``) so the same env-var / config-overlay /
# registry-default chain applies as everywhere else in kairix.


_TOPOLOGY_V2_CONFIG_FLAG = "topology_v2_config"
_CONNECTOR_SHAREPOINT_FLAG = "connector_sharepoint"
# F17 — the "flag off, check is inert" detail line shows up on every
# topology v2 / sharepoint check when the flag is off. Pull to a single
# constant so the wording stays uniform across the three checks.
_SKIPPED_FLAG_OFF_DETAIL_TEMPLATE = "skipped — {flag} flag is OFF (default-safe)"

# F17 — the three M365 secret names are referenced from both the
# credential-resolution loop and the failure-detail message.
_SHAREPOINT_SECRET_NAMES: tuple[str, ...] = (
    "connector-m365-tenant-id",  # pragma: allowlist secret — secret slot name, not a credential
    "connector-m365-client-id",  # pragma: allowlist secret — secret slot name, not a credential
    "connector-m365-client-secret",  # pragma: allowlist secret — secret slot name, not a credential
)


def _default_flag_reader(name: str) -> bool:
    """Production seam — defers to ``kairix.core.features.flag``.

    Lazy import so the onboard-check module stays importable on a fresh
    install where the registry module has heavy transitive deps.
    """
    from kairix.core.features import flag as _flag

    return _flag(name)


@dataclass
class TopologyV2CheckDeps:
    """Injectable dependencies for the topology v2 + SharePoint checks.

    Bundles the three DI seams the three v2026.5.24a1 checks need
    (feature-flag reader, config loader, DB cc_pair namer, secret
    reader) into a single Deps class so the check signatures stay free
    of test-only ``*_loader=None`` kwargs (F6).

    Production callers leave the dataclass at its default values; tests
    construct ``TopologyV2CheckDeps(flag_reader=..., config_loader=...,
    db_cc_pair_namer=..., secret_reader=...)`` to drive each check's
    branches without monkey-patching the live registry / config /
    secrets paths. Mirrors the existing :class:`OnboardChecksDeps`
    pattern in this module.

    Each field defaults via ``field(default_factory=...)`` so a
    consumer can override individual seams without re-specifying the
    others — the production defaults all delegate to the standard
    feature-flag resolver / config loader / SQLite reader / secret
    resolver.
    """

    flag_reader: Callable[[str], bool] = field(default_factory=lambda: _default_flag_reader)
    config_loader: Callable[[], dict[str, Any] | None] = field(default_factory=lambda: _default_overlay_path_loader)
    db_cc_pair_namer: Callable[[], frozenset[str]] = field(default_factory=lambda: _default_db_cc_pair_names)
    secret_reader: Callable[[str], str | None] = field(default_factory=lambda: _default_secret_reader)


def _default_overlay_path_loader() -> dict[str, Any] | None:
    """Production seam — loads the topology v2 section from kairix.config.yaml.

    Returns the parsed YAML dict (or None when no config file is found
    or the parse fails). The actual topology_v2 sub-section is read by
    the caller; this loader returns the full document so the caller
    sees the same shape ``kairix.config.topology_v2.parse_topology_v2``
    expects.

    Env-var read for the config path goes through
    ``kairix.paths.config_path_override`` so the F4 boundary holds
    (env reads live in paths.py / secrets.py only).

    Lazy yaml import so the module stays light when topology v2 is off.
    """
    import yaml as _yaml

    from kairix.paths import config_path_override

    config_path_str = config_path_override() or "kairix.config.yaml"
    p = Path(config_path_str).expanduser()
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = _yaml.safe_load(f) or {}
    except (OSError, _yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def check_topology_v2_config_valid(deps: TopologyV2CheckDeps | None = None) -> CheckResult:
    """topology_v2: block in kairix.config.yaml parses + passes cross-reference validation.

    When the ``topology_v2_config`` flag is OFF, returns ok=True with a
    "skipped" detail — the Wave D operator-config surface is inert by
    default and this check should not block fresh deployments.

    When ON, parses the ``topology_v2:`` block out of the active
    ``kairix.config.yaml`` (resolved via the standard ``KAIRIX_CONFIG_PATH``
    env-var or the ``kairix.config.yaml`` default), runs the 5
    cross-reference rules from
    ``kairix.config.topology_v2_validators.validate_topology_v2_references``,
    and reports either ok=True (zero failures) or ok=False with the
    failure messages compacted into the ``detail`` string.

    ``deps`` is the public DI seam (default :class:`TopologyV2CheckDeps`
    binds the production flag/config readers). Tests construct a Deps
    with substitute callables to drive the flag-OFF / parse-error /
    validation-failure / clean branches without touching the live
    config file.
    """
    d = deps if deps is not None else TopologyV2CheckDeps()
    if not d.flag_reader(_TOPOLOGY_V2_CONFIG_FLAG):
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
            ok=True,
            detail=_SKIPPED_FLAG_OFF_DETAIL_TEMPLATE.format(flag=_TOPOLOGY_V2_CONFIG_FLAG),
        )

    try:
        data = d.config_loader()
    except Exception as exc:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
            ok=False,
            detail=f"config loader raised: {exc}",
            fix="fix: ensure kairix.config.yaml exists and is readable. next: run `kairix config validate`.",
        )

    if data is None:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
            ok=False,
            detail="kairix.config.yaml not found — topology_v2_config flag is ON but no config to validate",
            fix=(
                "fix: create kairix.config.yaml at the repo root (or set KAIRIX_CONFIG_PATH) "
                "with a topology_v2: block per kairix.config.example.yaml. "
                "next: run `kairix config validate`."
            ),
        )

    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2
    from kairix.config.topology_v2_validators import validate_topology_v2_references

    try:
        parsed = parse_topology_v2(data)
    except TopologyV2ParseError as exc:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
            ok=False,
            detail=f"topology_v2 parse failed: {exc}",
            fix="fix: correct the YAML shape in kairix.config.yaml. next: run `kairix config validate`.",
        )

    failures = validate_topology_v2_references(parsed)
    if failures:
        summary = "; ".join(f.message for f in failures[:3])
        suffix = f" (+{len(failures) - 3} more)" if len(failures) > 3 else ""
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
            ok=False,
            detail=f"{len(failures)} topology_v2 cross-reference failure(s): {summary}{suffix}",
            fix=(
                "fix: declare the missing entries or remove the dangling references. "
                "next: run `kairix config validate`."
            ),
        )

    counts = (
        f"connectors={len(parsed.connectors)}, credentials={len(parsed.credentials)}, "
        f"cc_pairs={len(parsed.cc_pairs)}, collections={len(parsed.collections)}, "
        f"scope_profiles={len(parsed.scope_profiles)}, skills={len(parsed.skills)}"
    )
    return CheckResult(
        name=_CHECK_TOPOLOGY_V2_CONFIG_VALID,
        ok=True,
        detail=f"topology_v2 config valid ({counts})",
    )


def check_topology_v2_cc_pairs_registered(deps: TopologyV2CheckDeps | None = None) -> CheckResult:
    """Every declared cc_pair in kairix.config.yaml has a row in topology_cc_pairs.

    When the ``topology_v2_config`` flag is OFF, returns ok=True with a
    "skipped" detail.

    When ON, parses the declared cc_pair names from
    ``kairix.config.yaml`` and cross-checks against the live
    ``topology_cc_pairs`` table (read via the production
    ``list_cc_pairs`` helper). A declared cc_pair without a matching DB
    row means the apply-bridge hasn't run for it yet — typically the
    worker hasn't been restarted since the YAML edit (the apply-bridge
    runs at boot when ``topology_v2_config`` is on).

    ``deps`` is the public DI seam (default :class:`TopologyV2CheckDeps`
    binds the production flag / config / DB readers).
    """
    d = deps if deps is not None else TopologyV2CheckDeps()
    if not d.flag_reader(_TOPOLOGY_V2_CONFIG_FLAG):
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=True,
            detail=_SKIPPED_FLAG_OFF_DETAIL_TEMPLATE.format(flag=_TOPOLOGY_V2_CONFIG_FLAG),
        )

    try:
        data = d.config_loader()
    except Exception as exc:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=False,
            detail=f"config loader raised: {exc}",
            fix=(
                "fix: ensure kairix.config.yaml exists and is readable. "
                "next: restart the worker so the apply-bridge re-runs."
            ),
        )
    if data is None:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=True,
            detail="no kairix.config.yaml — nothing to register",
        )

    from kairix.config.topology_v2 import TopologyV2ParseError, parse_topology_v2

    try:
        parsed = parse_topology_v2(data)
    except TopologyV2ParseError as exc:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=False,
            detail=f"topology_v2 parse failed: {exc}",
            fix="fix: correct the YAML shape in kairix.config.yaml. next: run `kairix config validate`.",
        )

    declared_names = frozenset(p.name for p in parsed.cc_pairs)
    if not declared_names:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=True,
            detail="no cc_pairs declared in topology_v2 — nothing to register",
        )

    try:
        registered = d.db_cc_pair_namer()
    except Exception as exc:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=False,
            detail=f"topology_cc_pairs lookup failed: {exc}",
            fix=("fix: ensure the SQLite database is reachable. next: restart the worker so the apply-bridge re-runs."),
        )

    missing = sorted(declared_names - registered)
    if missing:
        return CheckResult(
            name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
            ok=False,
            detail=(f"{len(missing)} declared cc_pair(s) not registered in topology_cc_pairs: {', '.join(missing)}"),
            fix=(
                "fix: restart the worker — the apply-bridge runs at boot "
                "and materialises declared cc_pairs idempotently."
            ),
        )

    return CheckResult(
        name=_CHECK_TOPOLOGY_V2_CC_PAIRS_REGISTERED,
        ok=True,
        detail=f"{len(declared_names)} declared cc_pair(s) registered",
    )


def _default_db_cc_pair_names() -> frozenset[str]:
    """Production seam — return the set of cc_pair names from topology_cc_pairs."""
    from kairix.core.connectors.cc_pair import list_cc_pairs
    from kairix.core.db import get_db_path, open_db

    db = open_db(Path(get_db_path()))
    try:
        rows = list_cc_pairs(db)
    finally:
        db.close()
    return frozenset(row.name for row in rows)


def check_sharepoint_credentials_loaded(deps: TopologyV2CheckDeps | None = None) -> CheckResult:
    """SharePoint connector secrets resolve via kairix.secrets.get_secret.

    When the ``connector_sharepoint`` flag is OFF, returns ok=True with
    a "skipped" detail.

    When ON, attempts to resolve the three M365 client-credentials
    secrets the SharePoint connector requires:

      * ``connector-m365-tenant-id``
      * ``connector-m365-client-id``
      * ``connector-m365-client-secret``

    Reports ok=True iff all three resolve to non-empty strings; ok=False
    with the missing-name list otherwise. The secret resolution chain
    is the standard ``kairix.secrets.get_secret`` cascade (env var →
    per-file secret → bundle file → Azure Key Vault), so the same fix
    applies whether the operator is on Docker / pip / VM.

    ``deps`` is the public DI seam (default :class:`TopologyV2CheckDeps`
    binds the production flag + secret readers). Tests construct a
    Deps with substitute callables to drive the present / partial /
    missing branches without touching the live environment or Key
    Vault.
    """
    d = deps if deps is not None else TopologyV2CheckDeps()
    if not d.flag_reader(_CONNECTOR_SHAREPOINT_FLAG):
        return CheckResult(
            name=_CHECK_SHAREPOINT_CREDENTIALS_LOADED,
            ok=True,
            detail=_SKIPPED_FLAG_OFF_DETAIL_TEMPLATE.format(flag=_CONNECTOR_SHAREPOINT_FLAG),
        )

    missing: list[str] = []
    for name in _SHAREPOINT_SECRET_NAMES:
        try:
            value = d.secret_reader(name)
        except Exception:
            value = None
        if not value:
            missing.append(name)

    if missing:
        return CheckResult(
            name=_CHECK_SHAREPOINT_CREDENTIALS_LOADED,
            ok=False,
            detail=f"{len(missing)} SharePoint secret(s) unresolved: {', '.join(missing)}",
            fix=(
                "fix: set the three M365 client-credentials secrets that the SharePoint "
                "connector resolves via kairix.secrets.get_secret — "
                "`connector-m365-tenant-id`, `connector-m365-client-id`, and "
                "`connector-m365-client-secret`. next: re-run "
                "`kairix onboard check sharepoint_credentials_loaded`."
            ),
        )

    return CheckResult(
        name=_CHECK_SHAREPOINT_CREDENTIALS_LOADED,
        ok=True,
        detail=f"{len(_SHAREPOINT_SECRET_NAMES)} SharePoint secret(s) resolved",
    )


def _default_secret_reader(name: str) -> str | None:
    """Production seam — defers to ``kairix.secrets.get_secret``.

    Uses ``required=False`` so a missing secret returns ``None`` rather
    than raising — the calling check translates "any None" into a
    failure with the missing-name list.
    """
    from kairix.secrets import get_secret

    return get_secret(name, required=False)


ALL_CHECKS: list[Callable[..., CheckResult]] = [
    check_kairix_on_path,
    check_wrapper_installed,
    check_secrets_loaded,
    check_document_root_configured,
    check_vector_search_working,
    check_neo4j_reachable,
    check_agent_knowledge_populated,
    check_chunk_date_populated,
    check_mcp_service,
    check_query_cache_stats,
    check_embed_cache_stats,
    check_topology_v2_config_valid,
    check_topology_v2_cc_pairs_registered,
    check_sharepoint_credentials_loaded,
]


def run_all_checks(*, checks: list[Callable[..., CheckResult]] | None = None) -> list[CheckResult]:
    """Run all deployment checks in order. Returns results for all checks.

    Checks are ordered by dependency: PATH → secrets → vault → search → graph.
    A failure in an early check usually explains failures in later checks.

    ``checks`` is the public DI seam — tests pass a fake check list to
    drive the runner's collation logic without monkey-patching the
    module-level ``ALL_CHECKS`` registry. Production callers leave it
    ``None`` and the runner uses the canonical registry.
    """
    effective = checks if checks is not None else ALL_CHECKS
    results: list[CheckResult] = []
    for check_fn in effective:
        try:
            results.append(check_fn())
        except Exception as exc:
            results.append(
                CheckResult(
                    name=check_fn.__name__.removeprefix("check_"),
                    ok=False,
                    detail=f"Check raised unexpected exception: {exc}",
                    fix="This is a bug in kairix.platform.onboard.check — please report it.",
                )
            )
    return results


def run_onboard_check(*, checks: list[Callable[..., CheckResult]] | None = None) -> OnboardResult:
    """Run all deployment checks and return a structured OnboardResult.

    Canonical surface for:
      - ``kairix onboard check --json`` CLI output
      - docker-compose healthcheck (exit code is derived from .fully_passed)
      - any caller that needs to act on individual failures programmatically

    Each failed check produces a CheckFailure with a populated, non-empty
    ``remediation`` string sourced from _CANONICAL_REMEDIATIONS. The set of
    checks (and their order) is identical to run_all_checks() — this
    function only restructures the output. ``checks`` forwards through to
    ``run_all_checks`` as the public DI seam.
    """
    results = run_all_checks(checks=checks)
    failures = [
        CheckFailure(
            check=r.name,
            detail=r.detail,
            remediation=_remediation_for(r.name, r.fix),
        )
        for r in results
        if not r.ok
    ]
    passed = sum(1 for r in results if r.ok)
    total = len(results)
    return OnboardResult(
        passed=passed,
        total=total,
        failures=failures,
        fully_passed=(passed == total),
    )
