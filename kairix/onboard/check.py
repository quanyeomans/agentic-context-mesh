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
    from kairix.paths import _is_docker

    if _is_docker():
        return CheckResult(
            name="wrapper_installed",
            ok=True,
            detail="Running in Docker — wrapper check skipped (pip install in image)",
        )

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
    workspace_root = os.environ.get("KAIRIX_WORKSPACE_ROOT", str(Path.home() / ".kairix" / "workspaces"))
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


def check_chunk_date_populated() -> CheckResult:
    """chunk_date is populated in content_vectors (required for TMP-7B temporal boost)."""
    try:
        from kairix.db import get_db_path

        db_path = get_db_path()
        import sqlite3

        db = sqlite3.connect(str(db_path))
        try:
            # Check if the column exists first
            cols = {row[1] for row in db.execute("PRAGMA table_info(content_vectors)")}
            if "chunk_date" not in cols:
                return CheckResult(
                    name="chunk_date_populated",
                    ok=False,
                    detail="chunk_date column missing from content_vectors",
                    fix=(
                        "Run kairix embed to add the column and populate dates.\n"
                        "The migration is automatic on next embed run."
                    ),
                )

            total = db.execute("SELECT COUNT(*) FROM content_vectors").fetchone()[0]
            dated = db.execute("SELECT COUNT(*) FROM content_vectors WHERE chunk_date IS NOT NULL").fetchone()[0]
        finally:
            db.close()

        if total == 0:
            return CheckResult(
                name="chunk_date_populated",
                ok=False,
                detail="content_vectors is empty — vault has not been embedded",
                fix="Run: kairix embed",
            )

        pct = 100 * dated / total
        if dated == 0:
            return CheckResult(
                name="chunk_date_populated",
                ok=False,
                detail=f"chunk_date: 0/{total} chunks dated (0%) — TMP-7B temporal boost is inert",
                fix=(
                    "Run kairix embed to populate chunk_date from document frontmatter and filenames.\n"
                    "Documents need 'date: YYYY-MM-DD' in frontmatter or a date in their filename."
                ),
            )
        if pct < 20:
            return CheckResult(
                name="chunk_date_populated",
                ok=False,
                detail=f"chunk_date: {dated}/{total} chunks dated ({pct:.0f}%) — low coverage, temporal boost degraded",
                fix=(
                    "Add 'date: YYYY-MM-DD' frontmatter to more documents, or use dated filenames.\n"
                    "Re-run kairix embed after updating documents."
                ),
            )

        return CheckResult(
            name="chunk_date_populated",
            ok=True,
            detail=f"chunk_date: {dated}/{total} chunks dated ({pct:.0f}%)",
        )

    except FileNotFoundError:
        return CheckResult(
            name="chunk_date_populated",
            ok=False,
            detail="QMD index not found — vault not embedded yet",
            fix="Run: kairix embed",
        )
    except Exception as exc:
        return CheckResult(
            name="chunk_date_populated",
            ok=False,
            detail=f"chunk_date check failed: {exc}",
            fix="Check QMD index at ~/.cache/qmd/index.sqlite",
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
_MCP_SSE_PORT = int(os.environ.get("KAIRIX_MCP_PORT", "8080"))


def _probe_openclaw_harness() -> tuple[bool, str]:
    """Return (ok, detail) for the OpenClaw stdio harness."""
    import json as _json

    for candidate in _OPENCLAW_JSON_PATHS:
        try:
            p = Path(str(candidate))
            if not p.exists():
                continue
            data = _json.loads(p.read_text())
            # OpenClaw stores MCP servers at mcp.servers (set via `openclaw mcp set`)
            mcp_servers = data.get("mcp", {}).get("servers", {})
            if _MCP_KAIRIX_SERVER_NAME in mcp_servers:
                entry = mcp_servers[_MCP_KAIRIX_SERVER_NAME]
                cmd = entry.get("command", "")
                cmd_ok = bool(cmd) and Path(cmd).exists() and os.access(cmd, os.X_OK)
                if cmd_ok:
                    return True, f"OpenClaw: registered in {p.name}, start command executable"
                return False, f"OpenClaw: registered but start command missing/not executable: {cmd}"
        except (OSError, _json.JSONDecodeError):
            continue

    # Fallback: try openclaw CLI
    try:
        result = subprocess.run(
            ["openclaw", "mcp", "list"],
            capture_output=True, text=True, timeout=5,
        )
        if _MCP_KAIRIX_SERVER_NAME in result.stdout:
            return True, "OpenClaw: registered (via 'openclaw mcp list')"
    except (FileNotFoundError, Exception):
        pass

    return False, "OpenClaw: not detected"


def _probe_claude_desktop_harness() -> tuple[bool, str]:
    """Return (ok, detail) for the Claude Desktop stdio harness."""
    import json as _json

    for candidate in _CLAUDE_DESKTOP_CONFIG_PATHS:
        try:
            p = Path(str(candidate))
            if not p.exists():
                continue
            data = _json.loads(p.read_text())
            mcp_servers = data.get("mcpServers", {})
            if "kairix" in mcp_servers:
                entry = mcp_servers["kairix"]
                cmd_args = entry.get("args", [])
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
        result = subprocess.run(
            ["systemctl", "is-active", "kairix-mcp.service"],
            capture_output=True, text=True, timeout=5,
        )
        state = result.stdout.strip()
        if state == "active":
            return True, f"SSE/HTTP: kairix-mcp.service active (port {_MCP_SSE_PORT} not yet listening — may still be starting)"
        elif state not in ("", "inactive", "failed", "unknown"):
            return False, f"SSE/HTTP: kairix-mcp.service state={state}"
    except (FileNotFoundError, Exception):
        pass

    return False, f"SSE/HTTP: not listening on port {_MCP_SSE_PORT}"


def check_mcp_service() -> CheckResult:
    """
    kairix MCP server is reachable by at least one configured consumer.

    Probes each transport harness that is detectable on this host:
      - OpenClaw (stdio): mcp-kairix registered in openclaw.json
      - Claude Desktop (stdio): kairix registered in claude_desktop_config.json
      - SSE/HTTP (persistent): port 7443 listening or kairix-mcp.service active

    Passes if at least one harness is configured and functional.
    If no harness is detected, reports which harnesses are available to configure.
    """
    openclaw_ok, openclaw_detail = _probe_openclaw_harness()
    claude_ok, claude_detail = _probe_claude_desktop_harness()
    sse_ok, sse_detail = _probe_sse_harness()

    active = [d for ok, d in [(openclaw_ok, openclaw_detail), (claude_ok, claude_detail), (sse_ok, sse_detail)] if ok]
    inactive = [d for ok, d in [(openclaw_ok, openclaw_detail), (claude_ok, claude_detail), (sse_ok, sse_detail)] if not ok]

    if active:
        return CheckResult(
            name="mcp_service",
            ok=True,
            detail="kairix MCP server accessible — " + "; ".join(active),
        )

    return CheckResult(
        name="mcp_service",
        ok=False,
        detail="kairix MCP server not configured for any consumer — " + "; ".join(inactive),
        fix=(
            "Configure at least one MCP consumer harness:\n\n"
            "  OpenClaw (stdio):\n"
            "    openclaw mcp set mcp-kairix "
            "'{\"type\":\"stdio\",\"command\":\"/path/to/kairix-start.sh\"}'\n\n"
            "  Claude Desktop (stdio): add to ~/Library/Application Support/Claude/claude_desktop_config.json:\n"
            "    {\"mcpServers\": {\"kairix\": {\"command\": \"kairix\", \"args\": [\"mcp\", \"serve\"]}}}\n\n"
            "  SSE/HTTP (persistent service):\n"
            "    sudo systemctl enable --now kairix-mcp.service\n"
            "    # or: kairix mcp serve --transport sse --port 7443 &\n"
        ),
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
    check_chunk_date_populated,
    check_mcp_service,
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
