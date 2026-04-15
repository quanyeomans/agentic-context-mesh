"""
kairix.onboard.check — deployment health checks.

Each check is independent and returns a CheckResult with:
  name   — short identifier
  ok     — True if the check passed
  detail — human-readable explanation of status
  fix    — actionable remediation hint (None when ok=True)

run_all_checks() returns the full list. Checks are ordered from most-fundamental
(PATH, secrets) to most-dependent (vector search, entity graph) so failures are
diagnosed from the bottom up.

Failure modes:
  - Checks never raise; exceptions are caught and surfaced as failed CheckResult.
  - Checks that require live external services (Neo4j, Azure KV) degrade gracefully.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single deployment check."""

    name: str
    ok: bool
    detail: str
    fix: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_kairix_on_path() -> CheckResult:
    """kairix is findable via PATH."""
    path = shutil.which("kairix")
    if path is None:
        return CheckResult(
            name="kairix_on_path",
            ok=False,
            detail="kairix not found on PATH",
            fix=(
                "Add the kairix symlink directory to PATH.\n"
                "Run: bash scripts/deploy-vm.sh  (sets up /etc/profile.d/kairix.sh)\n"
                "Or manually: export PATH=/opt/openclaw/bin:$PATH"
            ),
        )
    return CheckResult(name="kairix_on_path", ok=True, detail=f"kairix found at {path}")


def check_wrapper_installed() -> CheckResult:
    """The kairix symlink points to a shell wrapper, not the raw Python binary."""
    path = shutil.which("kairix")
    if path is None:
        return CheckResult(
            name="wrapper_installed",
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
                name="wrapper_installed",
                ok=False,
                detail=f"kairix symlink points to raw Python binary: {resolved}",
                fix=(
                    "The symlink should point to kairix-wrapper.sh, not the Python binary.\n"
                    "Run the deploy script to fix:\n"
                    "  bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/deploy-vm.sh)\n"
                    "This installs the wrapper at /opt/kairix/bin/kairix-wrapper.sh\n"
                    "and updates /usr/local/bin/kairix to point to it."
                ),
            )
        if first_line.startswith("#!") and ("bash" in first_line or "sh" in first_line):
            return CheckResult(name="wrapper_installed", ok=True, detail=f"wrapper installed at {resolved}")

        return CheckResult(
            name="wrapper_installed",
            ok=False,
            detail=f"kairix binary has unexpected format (header: {first_line[:60]})",
            fix="Run scripts/deploy-vm.sh to reinstall the wrapper.",
        )
    except Exception as exc:
        return CheckResult(
            name="wrapper_installed",
            ok=False,
            detail=f"Cannot read kairix binary at {resolved}: {exc}",
            fix="Check file permissions on the kairix binary.",
        )


_REQUIRED_SECRETS = ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")
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


def check_secrets_loaded() -> CheckResult:
    """Azure OpenAI credentials are available in the environment or a secrets file."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")

    # Tier 1 — credentials in process environment (wrapper loaded them)
    if api_key and endpoint:
        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
        return CheckResult(
            name="secrets_loaded",
            ok=True,
            detail=f"Azure credentials present (key: {masked_key}, endpoint: {endpoint[:40]}...)",
        )

    # Tier 2 — probe secrets file directly (credentials present but not yet in env;
    # load_secrets() is called lazily on first kairix._azure import)
    secrets_file_env = os.environ.get("KAIRIX_SECRETS_FILE", "")
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
                name="secrets_loaded",
                ok=True,
                detail=(
                    f"Secrets file found at {probe} — credentials will be active on first search call. "
                    f"Run `kairix search` to confirm."
                ),
            )
        # File exists but is missing keys — give specific guidance
        return CheckResult(
            name="secrets_loaded",
            ok=False,
            detail=f"Secrets file at {probe} is missing required keys: {', '.join(missing_in_file)}",
            fix=(
                f"Add the missing keys to {probe}:\n"
                + "".join(f"  {k}=<value>\n" for k in missing_in_file)
                + "Fetch from Azure Key Vault:\n"
                "  az keyvault secret show --vault-name <kv> --name azure-openai-api-key --query value -o tsv"
            ),
        )

    # Tier 3 — nothing found
    missing_env = [k for k in _REQUIRED_SECRETS if not os.environ.get(k)]
    default_path = _SECRETS_FILE_PROBE_PATHS[-1]
    return CheckResult(
        name="secrets_loaded",
        ok=False,
        detail=f"Azure credentials not found in environment or secrets file: {', '.join(missing_env)}",
        fix=(
            f"Create {default_path} with:\n"
            "  AZURE_OPENAI_API_KEY=<value>\n"
            "  AZURE_OPENAI_ENDPOINT=<value>\n"
            "Or ensure the kairix wrapper (not the raw Python binary) is on PATH.\n"
            "Verify: head -1 $(which kairix)  — should show #!/usr/bin/env bash"
        ),
    )


def check_vault_root_configured() -> CheckResult:
    """KAIRIX_VAULT_ROOT is set and the directory exists."""
    vault_root = os.environ.get("KAIRIX_VAULT_ROOT") or os.environ.get("VAULT_ROOT", "")
    if not vault_root:
        return CheckResult(
            name="vault_root_configured",
            ok=False,
            detail="KAIRIX_VAULT_ROOT is not set",
            fix=("Set KAIRIX_VAULT_ROOT in /opt/kairix/service.env:\n  KAIRIX_VAULT_ROOT=/data/obsidian-vault"),
        )
    p = Path(vault_root)
    if not p.exists():
        return CheckResult(
            name="vault_root_configured",
            ok=False,
            detail=f"KAIRIX_VAULT_ROOT directory does not exist: {vault_root}",
            fix=(
                "Create the directory or update KAIRIX_VAULT_ROOT in /opt/kairix/service.env.\n"
                "If your vault is at a different path, set: KAIRIX_VAULT_ROOT=/your/vault/path"
            ),
        )
    md_count = sum(1 for _ in p.rglob("*.md") if not _.name.startswith("."))
    return CheckResult(
        name="vault_root_configured",
        ok=True,
        detail=f"Vault root: {vault_root} ({md_count:,} .md files found)",
    )


def check_vector_search_working() -> CheckResult:
    """Vector search returns results with vec_count > 0 (not BM25-only fallback)."""
    try:
        from kairix.search.hybrid import search

        result = search(query="knowledge management", budget=500)

        vec_count = getattr(result, "vec_count", None)
        bm25_count = getattr(result, "bm25_count", None)
        vec_failed = getattr(result, "vec_failed", None)
        result_count = len(result.results) if hasattr(result, "results") else 0

        if vec_failed:
            return CheckResult(
                name="vector_search_working",
                ok=False,
                detail=(
                    f"Vector search failed (vec_failed=True). "
                    f"Results: {result_count} (BM25 only). bm25={bm25_count}, vec=0"
                ),
                fix=(
                    "Vector search failure usually means Azure credentials aren't loaded.\n"
                    "Check: kairix onboard check  — look at secrets_loaded result.\n"
                    "If secrets are loaded, check the embed ran:\n"
                    "  sqlite3 ~/.cache/qmd/index.sqlite 'SELECT COUNT(*) FROM vectors_vec;'\n"
                    "  Should be > 0. If 0: run kairix embed --limit 20 to test."
                ),
            )

        if vec_count is not None and vec_count == 0 and result_count == 0:
            return CheckResult(
                name="vector_search_working",
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
            name="vector_search_working",
            ok=True,
            detail=f"Vector search working ({', '.join(detail_parts)})",
        )

    except Exception as exc:
        return CheckResult(
            name="vector_search_working",
            ok=False,
            detail=f"Search raised an exception: {exc}",
            fix=(
                "Check AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT are set.\n"
                "Run: kairix onboard check  to see secrets_loaded status."
            ),
        )


def check_neo4j_reachable() -> CheckResult:
    """Neo4j is reachable and contains entities."""
    try:
        from kairix.graph.client import get_client

        client = get_client()
        if not getattr(client, "available", False):
            return CheckResult(
                name="neo4j_reachable",
                ok=False,
                detail="Neo4j client unavailable (KAIRIX_NEO4J_URI not set or connection refused)",
                fix=(
                    "Install Neo4j:\n"
                    "  bash <(curl -fsSL https://raw.githubusercontent.com/quanyeomans/agentic-context-mesh/main/scripts/install-neo4j.sh)\n"
                    "Or run with Docker:\n"
                    "  docker run -d --name neo4j -p 7687:7687 -e NEO4J_AUTH=neo4j/changeme neo4j:5-community\n"
                    "Then set KAIRIX_NEO4J_URI in /opt/kairix/service.env:\n"
                    "  KAIRIX_NEO4J_URI=bolt://localhost:7687\n"
                    "Neo4j is optional — entity boost and multi-hop queries are degraded without it."
                ),
            )

        rows = client.cypher("MATCH (n) RETURN count(n) AS total LIMIT 1")
        total = rows[0]["total"] if rows else 0

        if total == 0:
            return CheckResult(
                name="neo4j_reachable",
                ok=False,
                detail="Neo4j reachable but empty — vault crawler has not run",
                fix=(
                    "Populate the entity graph:\n"
                    "  kairix vault crawl --vault-root $KAIRIX_VAULT_ROOT\n"
                    "Expected: ≥ 50 nodes for a typical vault."
                ),
            )

        return CheckResult(
            name="neo4j_reachable",
            ok=True,
            detail=f"Neo4j reachable — {total:,} nodes in graph",
        )

    except Exception as exc:
        return CheckResult(
            name="neo4j_reachable",
            ok=False,
            detail=f"Neo4j check failed: {exc}",
            fix=(
                "Verify Neo4j connection details in /opt/kairix/service.env.\n"
                "kairix degrades gracefully when Neo4j is unavailable — "
                "entity boost and multi-hop are disabled but search still works."
            ),
        )


def check_agent_knowledge_populated() -> CheckResult:
    """At least one agent has memory logs (required for briefing pipeline)."""
    workspace_root = os.environ.get("KAIRIX_WORKSPACE_ROOT", "/data/workspaces")
    p = Path(workspace_root)
    if not p.exists():
        return CheckResult(
            name="agent_knowledge_populated",
            ok=False,
            detail=f"Workspace root not found: {workspace_root}",
            fix=(
                f"Create workspace directories:\n"
                f"  sudo mkdir -p {workspace_root}/<agent-name>/memory\n"
                f"  sudo chown -R $(whoami) {workspace_root}\n"
                f"Or set KAIRIX_WORKSPACE_ROOT in /opt/kairix/service.env."
            ),
        )

    # Look for any memory log files
    memory_files = list(p.rglob("*/memory/*.md"))
    if not memory_files:
        return CheckResult(
            name="agent_knowledge_populated",
            ok=False,
            detail=f"No agent memory logs found under {workspace_root}",
            fix=(
                "Agent memory logs are written by agents during sessions.\n"
                "Expected path pattern: {workspace_root}/{agent}/memory/YYYY-MM-DD.md\n"
                "This is informational — search works without memory logs.\n"
                "Briefing synthesis (kairix brief) requires at least some memory content."
            ),
        )

    return CheckResult(
        name="agent_knowledge_populated",
        ok=True,
        detail=f"Agent memory logs found: {len(memory_files)} files under {workspace_root}",
    )


# ---------------------------------------------------------------------------
# Run all checks
# ---------------------------------------------------------------------------


ALL_CHECKS = [
    check_kairix_on_path,
    check_wrapper_installed,
    check_secrets_loaded,
    check_vault_root_configured,
    check_vector_search_working,
    check_neo4j_reachable,
    check_agent_knowledge_populated,
]


def run_all_checks() -> list[CheckResult]:
    """Run all deployment checks in order. Returns results for all checks.

    Checks are ordered by dependency: PATH → secrets → vault → search → graph.
    A failure in an early check usually explains failures in later checks.
    """
    results: list[CheckResult] = []
    for check_fn in ALL_CHECKS:
        try:
            results.append(check_fn())
        except Exception as exc:
            results.append(
                CheckResult(
                    name=check_fn.__name__.removeprefix("check_"),
                    ok=False,
                    detail=f"Check raised unexpected exception: {exc}",
                    fix="This is a bug in kairix.onboard.check — please report it.",
                )
            )
    return results
