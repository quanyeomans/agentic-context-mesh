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
import subprocess
import sys
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


def check_secrets_loaded() -> CheckResult:
    """Azure OpenAI credentials are available in the environment."""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")

    missing = []
    if not api_key:
        missing.append("AZURE_OPENAI_API_KEY")
    if not endpoint:
        missing.append("AZURE_OPENAI_ENDPOINT")

    if missing:
        return CheckResult(
            name="secrets_loaded",
            ok=False,
            detail=f"Missing Azure credentials: {', '.join(missing)}",
            fix=(
                "Secrets should be loaded automatically by the kairix wrapper.\n"
                "Check that:\n"
                "  1. /opt/kairix/service.env has KAIRIX_KV_NAME set\n"
                "  2. /run/secrets/kairix.env exists (Docker sidecar) OR\n"
                "     Azure CLI is authenticated (az account show works)\n"
                "  3. The symlink points to kairix-wrapper.sh, not the Python binary\n"
                "Run: kairix onboard check  for full diagnostics"
            ),
        )

    # Mask the key in output
    masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"
    return CheckResult(
        name="secrets_loaded",
        ok=True,
        detail=f"Azure credentials present (key: {masked_key}, endpoint: {endpoint[:40]}...)",
    )


def check_vault_root_configured() -> CheckResult:
    """KAIRIX_VAULT_ROOT is set and the directory exists."""
    vault_root = os.environ.get("KAIRIX_VAULT_ROOT") or os.environ.get("VAULT_ROOT", "")
    if not vault_root:
        return CheckResult(
            name="vault_root_configured",
            ok=False,
            detail="KAIRIX_VAULT_ROOT is not set",
            fix=(
                "Set KAIRIX_VAULT_ROOT in /opt/kairix/service.env:\n"
                "  KAIRIX_VAULT_ROOT=/data/obsidian-vault"
            ),
        )
    p = Path(vault_root)
    if not p.exists():
        return CheckResult(
            name="vault_root_configured",
            ok=False,
            detail=f"KAIRIX_VAULT_ROOT directory does not exist: {vault_root}",
            fix=(
                f"Create the directory or update KAIRIX_VAULT_ROOT in /opt/kairix/service.env.\n"
                f"If your vault is at a different path, set: KAIRIX_VAULT_ROOT=/your/vault/path"
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
        from kairix.search.hybrid import search  # type: ignore[import]

        result = search(query="knowledge management", budget=500)

        vec_count = getattr(result, "vec_count", None)
        bm25_count = getattr(result, "bm25_count", None)
        vec_failed = getattr(result, "vec_failed", None)
        result_count = len(result.results) if hasattr(result, "results") else 0

        if vec_failed:
            return CheckResult(
                name="vector_search_working",
                ok=False,
                detail=f"Vector search failed (vec_failed=True). Results: {result_count} (BM25 only). bm25={bm25_count}, vec=0",
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
        from kairix.graph.client import get_client  # type: ignore[import]

        client = get_client()
        if not getattr(client, "available", False):
            return CheckResult(
                name="neo4j_reachable",
                ok=False,
                detail="Neo4j client unavailable (KAIRIX_NEO4J_URI not set or connection refused)",
                fix=(
                    "Set KAIRIX_NEO4J_URI in /opt/kairix/service.env:\n"
                    "  KAIRIX_NEO4J_URI=bolt://localhost:7687\n"
                    "Ensure Neo4j is running: systemctl status neo4j\n"
                    "Without Neo4j: entity boost and multi-hop queries are degraded."
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
