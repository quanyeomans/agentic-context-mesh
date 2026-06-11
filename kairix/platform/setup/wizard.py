"""Interactive setup wizard for first-time kairix configuration.

Walks through LLM credentials, document source, knowledge graph,
search preset, and initial indexing. Produces a kairix.config.yaml.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kairix.platform.setup.prompts import SetupContext, prompt, prompt_choice, prompt_yn

logger = logging.getLogger(__name__)

# Old _prompt, _prompt_choice, _prompt_yn removed — replaced by
# kairix.platform.setup.prompts which supports interactive, non-interactive, and JSON modes.


def _default_set_llm_endpoint(endpoint: str) -> None:
    """Production seam — defers `kairix.secrets.set_llm_endpoint` to call time."""
    from kairix.secrets import set_llm_endpoint

    set_llm_endpoint(endpoint)


def _default_set_llm_api_key(api_key: str) -> None:
    """Production seam — defers `kairix.secrets.set_llm_api_key` to call time."""
    from kairix.secrets import set_llm_api_key

    set_llm_api_key(api_key)


def _default_get_provider(name: str) -> Any:
    """Production seam — resolve the chosen provider plugin by name."""
    from kairix.providers import get_provider

    return get_provider(name)


def _default_hydrate(path: Path) -> int:
    """Production seam — hydrate the just-written bundle into the process env.

    Routes through :func:`kairix.secrets.refresh_secrets` so the
    in-process connection test resolves the persisted canonical values
    through the standard loader chain.
    """
    from kairix.secrets import refresh_secrets

    return refresh_secrets(path)


# Legacy (pre-Foundry) Azure endpoint host fragments — mirrors the
# detection the credentials layer uses for SDK-shape routing.
_LEGACY_AZURE_ENDPOINT_FRAGMENTS = ("openai.azure.com", "cognitiveservices.azure.com")
_FOUNDRY_ENDPOINT_FRAGMENT = "services.ai.azure.com"
_OPENAI_DEFAULT_ENDPOINT = "https://api.openai.com/v1"


def provider_plugin_name(provider_key: str, endpoint: str) -> str:
    """Map the wizard's provider survey answer to a provider plugin name.

    The returned name is what lands in the generated config's
    ``provider:`` field and what the connection test resolves through
    the plugin registry. Azure answers are split by endpoint shape:
    ``<r>.openai.azure.com`` / ``<r>.cognitiveservices.azure.com`` ride
    the ``azure_legacy`` plugin; everything else (including the
    recommended ``<r>.services.ai.azure.com``) rides ``azure_foundry``.
    "Other OpenAI-compatible endpoint" rides the ``openai`` plugin,
    which takes the stored endpoint verbatim as its base URL.
    """
    if provider_key == "azure":
        ep = endpoint.lower()
        is_legacy = any(fragment in ep for fragment in _LEGACY_AZURE_ENDPOINT_FRAGMENTS)
        if is_legacy and _FOUNDRY_ENDPOINT_FRAGMENT not in ep:
            return "azure_legacy"
        return "azure_foundry"
    return "openai"


_LLM_API_KEY_SECRET = "kairix-provider-llm-api-key"  # noqa: S105 — secret SLOT name, not a value  # pragma: allowlist secret
_LLM_ENDPOINT_SECRET = "kairix-provider-llm-endpoint"  # noqa: S105 — secret SLOT name, not a value  # pragma: allowlist secret
_EMBED_MODEL_SECRET = "kairix-provider-embed-model"  # noqa: S105 — secret SLOT name, not a value  # pragma: allowlist secret


def persist_llm_credentials(
    api_key: str,
    endpoint: str,
    embed_model: str,
    *,
    bundle_path: Path | None = None,
    hydrate_fn: Callable[[Path], int] = _default_hydrate,
) -> Path | None:
    """Persist the wizard's collected credentials under canonical names.

    Routes every value through :func:`kairix.secrets.set_secret` — the
    same use-case function behind ``kairix secrets set`` — so the wizard
    and the CLI share one persistence path (#473/#474). Empty values are
    skipped. After the last write the bundle is hydrated into the
    process env (``hydrate_fn`` seam; production = ``refresh_secrets``)
    so the connection test resolves the stored values.

    Returns the bundle path written to, or ``None`` when every value
    was empty (nothing persisted, nothing hydrated).
    """
    from kairix.secrets import set_secret

    pairs = (
        (_LLM_API_KEY_SECRET, api_key),
        (_LLM_ENDPOINT_SECRET, endpoint),
        (_EMBED_MODEL_SECRET, embed_model),
    )
    path: Path | None = None
    for name, value in pairs:
        if value:
            path = set_secret(name, value, bundle_path=bundle_path)
    if path is not None:
        hydrate_fn(path)
    return path


def probe_llm_connection(
    provider: str,
    endpoint: str,
    api_key: str,
    embed_model: str,
    *,
    set_llm_endpoint_fn: Callable[[str], None] = _default_set_llm_endpoint,
    set_llm_api_key_fn: Callable[[str], None] = _default_set_llm_api_key,
    get_provider_fn: Callable[[str], Any] = _default_get_provider,
) -> bool:
    """Test LLM connectivity with a single embed + chat call.

    ``provider`` is the chosen provider plugin name (the value the
    wizard writes to the config's ``provider:`` field); the probe
    resolves it through the plugin registry directly so a fresh machine
    doesn't depend on any pre-existing config discovery. Credentials
    resolve through the canonical loader chain — ``run_setup`` persists
    and hydrates them before this probe fires.

    Public surface for the wizard's connectivity probe — wired through
    :class:`WizardDeps` ``connection_test`` so production callers leave
    the defaults and ``run_setup`` invokes it transparently. Exposed
    directly so tests can exercise the failure branches without
    reaching past a ``_``-prefix (F5).

    ``set_llm_endpoint_fn`` / ``set_llm_api_key_fn`` / ``get_provider_fn``
    are public DI seams — production callers leave the defaults; tests
    pass fakes (raising or canned) to drive each branch without
    monkey-patching ``kairix.secrets`` or the plugin registry.

    On failure the underlying provider error is printed VERBATIM with an
    F21 affordance — a DNS failure, a wrong deployment name, or a
    missing config must never be reported as "bad credentials".

    ``embed_model`` is accepted for signature symmetry with the wizard's
    other provider-probe helpers (which DO use it); F19 ack.
    """
    _ = embed_model
    try:
        if endpoint:
            set_llm_endpoint_fn(endpoint)
        if api_key:
            set_llm_api_key_fn(api_key)

        plugin = get_provider_fn(provider)
        # Test embed
        vectors = plugin.embed_batch(["test connection"])
        if not vectors or not vectors[0] or len(vectors[0]) < 100:
            print("  Warning: embedding returned fewer dimensions than expected")
            return False
        # Test chat
        response = plugin.chat(
            [{"role": "user", "content": "Say 'ok' and nothing else."}],
            max_tokens=5,
        )
        if not response:
            print("  Warning: chat returned empty response")
            return False
        return True
    except Exception as exc:
        logger.warning("wizard: connection check failed — %s", exc)
        print(f"  Connection test failed: {exc}")
        print("  The error above is the root cause — it can be the endpoint, the model")
        print("  deployment name, or the network; your API key may be fine.")
        print("  fix: correct the failing value, then store it with: kairix secrets set <name>")
        print("  next: kairix secrets verify")
        print("  run: kairix setup")
        return False


def count_documents(path: str) -> tuple[int, float]:
    """Count markdown files and total size in MB."""
    p = Path(path)
    if not p.is_dir():
        return 0, 0.0
    files = list(p.rglob("*.md"))
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    return len(files), total_bytes / (1024 * 1024)


def load_template(name: str) -> dict[str, Any]:
    """Load an ontology template by name."""
    template_dir = Path(__file__).parent / "templates"
    template_path = template_dir / f"{name}.yaml"
    if not template_path.exists():
        return {}
    with open(template_path) as f:
        return yaml.safe_load(f) or {}


@dataclass
class WizardDeps:
    """Injectable dependencies for ``run_setup``.

    Replaces the F6-violating ``connection_test_fn=None`` test-only kwarg
    with a typed dataclass. Production code calls ``run_setup`` without
    ``deps`` — the default factory wires the real LLM connection probe.
    Tests construct ``WizardDeps(connection_test=lambda *_a, **_k: True)``
    and pass it through.

    The field is non-Optional with a ``default_factory`` (per CLAUDE.md
    F6 guidance) so mypy sees the production callable directly — no
    ``assert deps.x is not None`` ladder is needed inside the wizard.
    """

    connection_test: Callable[[str, str, str, str], bool] = field(default_factory=lambda: probe_llm_connection)
    # Public DI seam for the initial-embed run — tests inject a fake to
    # exercise the "indexing failed" branch in _maybe_run_initial_index
    # without monkey-patching ``kairix.core.embed.cli.main``. The default
    # factory lazy-imports the production ``embed_main`` at call time.
    embed_main: Callable[[], Any] = field(default_factory=lambda: _default_embed_main)
    # Public DI seam for credential persistence (#474). The production
    # default writes the collected (api_key, endpoint, embed_model)
    # through kairix.secrets.set_secret and hydrates the bundle; tests
    # inject a recorder so no real bundle file or process env is touched.
    persist_credentials: Callable[[str, str, str], Path | None] = field(
        default_factory=lambda: persist_llm_credentials,
    )


_USE_CASE_OPTIONS = [
    "Personal knowledge base (notes, journals, research)",
    "Technical documentation (code, runbooks, APIs)",
    "Business / consulting (clients, projects, proposals)",
    "Agent memory (OpenClaw, Claude Code, LangGraph)",
    "Just exploring (use the reference library)",
]
# Maps the use-case index to the matching template preset key.
_USE_CASE_TO_PRESET = ["general", "technical", "consulting", "general", "general"]

_PROVIDER_OPTIONS = [
    "Azure OpenAI (recommended for enterprise)",
    "OpenAI",
    "Other OpenAI-compatible endpoint",
]
_PROVIDER_KEYS = ["azure", "openai", "custom"]

_STORAGE_OPTIONS = [
    "Default location (~/.cache/kairix/) — good for personal use",
    "Custom path — for shared or production deployments",
    "FHS layout (/var/lib/kairix) — for system installs and v2026.6.8+ containers",
]

_COLLECTION_OPTIONS = [
    "Search everything — all documents in one collection (simplest)",
    "Use template collections (based on your preset above)",
    "Include agent workspace memories (for agent platforms)",
    "Skip — I'll configure collections later",
]

_AGENT_OPTIONS = [
    "Claude Desktop / Claude Code (stdio MCP)",
    "OpenClaw or similar agent platform (stdio MCP)",
    "Docker / HTTP service (SSE MCP on port 8080)",
    "Direct Python import (no MCP server needed)",
    "Skip — I'll configure this later",
]


def _resolve_preset(ctx: SetupContext, preset: str | None) -> str:
    """Step 0: pick the template preset from CLI flag or use-case survey."""
    if preset is not None:
        return preset if preset != "daily-log" else "general"
    idx = prompt_choice(ctx, "What are you setting up kairix for?", _USE_CASE_OPTIONS, default=0)
    return _USE_CASE_TO_PRESET[idx]


def _prompt_llm_credentials(ctx: SetupContext) -> tuple[str, str, str, str]:
    """Step 1a: gather (provider_key, endpoint, api_key, embed_model)."""
    provider_idx = prompt_choice(ctx, "Which LLM provider are you using?", _PROVIDER_OPTIONS, default=0)
    provider_key = _PROVIDER_KEYS[provider_idx]
    if provider_key == "azure":
        endpoint = prompt(ctx, "Azure OpenAI endpoint")
        api_key = prompt(ctx, "API key")
        embed_model = prompt(ctx, "Embedding model deployment name", "text-embedding-3-large")
        prompt(ctx, "Chat model deployment name", "gpt-4o-mini")  # future config expansion
    elif provider_key == "openai":
        endpoint = ""
        api_key = prompt(ctx, "OpenAI API key")
        embed_model = prompt(ctx, "Embedding model", "text-embedding-3-large")
        prompt(ctx, "Chat model", "gpt-4o-mini")  # future config expansion
    else:
        endpoint = prompt(ctx, "Endpoint URL")
        api_key = prompt(ctx, "API key")
        embed_model = prompt(ctx, "Embedding model name")
        prompt(ctx, "Chat model name")  # future config expansion
    return provider_key, endpoint, api_key, embed_model


def _confirm_llm_connection(
    ctx: SetupContext,
    deps: WizardDeps,
    provider_plugin: str,
    endpoint: str,
    api_key: str,
    embed_model: str,
) -> bool:
    """Test the connection against the just-written config + secrets.

    Runs AFTER the config write + credential persistence so the probe
    exercises what the operator will actually run with. On failure the
    probe has already printed the underlying provider error verbatim —
    this wrapper must not re-blame the operator's credentials.
    """
    print("\n  Testing connection to your provider...")
    if deps.connection_test(provider_plugin, endpoint, api_key, embed_model):
        print("  ✓ Connected successfully\n")
        return True
    print("  ✗ Connection test failed — the provider error above shows the cause.")
    print("  Your config and stored credentials are saved; nothing you entered is lost.\n")
    # Non-interactive mode is for CI/Docker/scripted bootstrap where the
    # operator can't answer a prompt; default to continuing so a config
    # is still emitted. Interactive operators retain the safer default.
    continue_default = not ctx.interactive
    return prompt_yn(ctx, "Continue anyway?", default=continue_default)


def _read_back_credentials(
    secrets_path: Path | None,
    endpoint: str,
    api_key: str,
) -> tuple[str, str]:
    """Read the persisted credentials back from the bundle file.

    The connection test must validate what was STORED (what every later
    command resolves), not the in-memory copies — a persistence bug
    would otherwise pass the probe and fail on first real use. Falls
    back to the collected values when nothing was persisted.
    """
    if secrets_path is None or not secrets_path.exists():
        return endpoint, api_key
    from kairix.secrets import load_secrets_file

    stored = load_secrets_file(secrets_path)
    return (
        stored.get("KAIRIX_PROVIDER_LLM_ENDPOINT") or endpoint,
        stored.get("KAIRIX_PROVIDER_LLM_API_KEY") or api_key,
    )


def _resolve_document_root(ctx: SetupContext, document_path: str | None) -> str | None:
    """Step 2: resolve & validate the document root. Returns None on missing dir."""
    if document_path:
        doc_root = os.path.expanduser(document_path)
    else:
        doc_root = prompt(
            ctx,
            "Where are your documents? (path to folder)",
            default=str(Path.home() / "Documents"),
        )
        doc_root = os.path.expanduser(doc_root)
    if not os.path.isdir(doc_root):
        print(f"\n  Error: '{doc_root}' does not exist or is not a directory.")
        print("  Create the folder first, then re-run setup.\n")
        return None
    return doc_root


def _resolve_storage_dir(ctx: SetupContext) -> str:
    """Step 3: pick the data directory based on the storage option."""
    idx = prompt_choice(ctx, "Where should kairix store its data?", _STORAGE_OPTIONS)
    if idx == 0:
        return str(Path.home() / ".cache" / "kairix")
    if idx == 1:
        return os.path.expanduser(prompt(ctx, "Data directory path"))
    # Docker / FHS — the v2026.6.8+ unified container + system install both
    # land here per Plan 1's kairix init.
    return "/var/lib/kairix"


def _prompt_neo4j(ctx: SetupContext) -> tuple[bool, str]:
    """Step 4: knowledge-graph (Neo4j) selection. Returns (enabled, uri)."""
    if not prompt_yn(ctx, "\n  Enable knowledge graph?", default=True):
        return False, ""
    neo4j_uri = prompt(ctx, "Neo4j URI", "bolt://localhost:7687")
    try:
        from kairix.knowledge.graph.client import Neo4jClient

        client = Neo4jClient.__new__(Neo4jClient)
        client._uri = neo4j_uri
        print("  ✓ Neo4j URI configured\n")
    except Exception:
        print("  Note: Neo4j connection will be tested when the service starts\n")
    return True, neo4j_uri


_MARKDOWN_GLOB = "**/*.md"
_PRESET_COLLECTIONS: dict[str, list[dict[str, str]]] = {
    "consulting": [
        {"name": "clients", "path": "Clients", "glob": _MARKDOWN_GLOB},
        {"name": "projects", "path": "Projects", "glob": _MARKDOWN_GLOB},
        {"name": "knowledge", "path": "Knowledge", "glob": _MARKDOWN_GLOB},
        {"name": "entities", "path": "Entities", "glob": _MARKDOWN_GLOB},
    ],
    "technical": [
        {"name": "docs", "path": "docs", "glob": _MARKDOWN_GLOB},
        {"name": "runbooks", "path": "runbooks", "glob": _MARKDOWN_GLOB},
        {"name": "reference", "path": "reference", "glob": _MARKDOWN_GLOB},
    ],
}


def _build_workspace_collections() -> dict[str, Any]:
    """Build the all-docs + agent-workspaces collection config."""
    from kairix.paths import workspace_root as _ws_root_fn

    workspace_root = str(_ws_root_fn())
    print(f"  ✓ Documents + agent workspace memories ({workspace_root}) configured.\n")
    return {
        "shared": [
            {"name": "all", "path": ".", "glob": _MARKDOWN_GLOB},
            {"name": "workspaces", "path": workspace_root, "glob": "**/memory/**/*.md"},
        ],
    }


def _resolve_collections(ctx: SetupContext, preset_key: str) -> dict[str, Any] | None:
    """Step 6: collection-organisation choice. Returns None on 'skip'."""
    idx = prompt_choice(ctx, "How do you want to organise your documents?", _COLLECTION_OPTIONS)
    if idx == 0:
        print("  ✓ All documents will be searchable.\n")
        return {"shared": [{"name": "all", "path": ".", "glob": _MARKDOWN_GLOB}]}
    if idx == 1:
        shared = _PRESET_COLLECTIONS.get(preset_key, [{"name": "all", "path": ".", "glob": _MARKDOWN_GLOB}])
        config = {"shared": shared}
        print(f"  ✓ {len(shared)} collections configured.\n")
        return config
    if idx == 2:
        return _build_workspace_collections()
    return None


def _print_claude_desktop_instructions() -> None:
    """Print Claude Desktop / Code MCP wiring instructions."""
    import platform as _platform

    if _platform.system() == "Darwin":
        config_path_hint = "~/Library/Application Support/Claude/claude_desktop_config.json"
    else:
        config_path_hint = "~/.config/Claude/claude_desktop_config.json"
    print(f"\n  To connect Claude Desktop, add this to:\n  {config_path_hint}\n")
    print("  {")
    print('    "mcpServers": {')
    print('      "kairix": {')
    print('        "command": "kairix",')
    print('        "args": ["mcp", "serve"]')
    print("      }")
    print("    }")
    print("  }\n")


def _print_sse_instructions() -> None:
    """Print SSE MCP server (Docker/HTTP) startup hint."""
    from kairix.platform.onboard.ports import find_available_port, is_port_available

    default_port = 8080
    if is_port_available(default_port):
        mcp_port = default_port
    else:
        mcp_port = find_available_port(preferred=default_port)
        print(f"\n  Port {default_port} is in use — suggesting {mcp_port} instead.")
    print(f"\n  MCP endpoint: http://localhost:{mcp_port}")
    print(f"  Start with: kairix mcp serve --transport sse --port {mcp_port}\n")


def _print_agent_instructions(ctx: SetupContext) -> None:
    """Step 7: agent-platform integration hints."""
    idx = prompt_choice(ctx, "Select your agent platform:", _AGENT_OPTIONS)
    if idx == 0:
        _print_claude_desktop_instructions()
    elif idx == 1:
        print('\n  Run: openclaw mcp set mcp-kairix "kairix mcp serve"\n')
    elif idx == 2:
        _print_sse_instructions()
    elif idx == 3:
        print("\n  Import directly in Python:")
        print("  from kairix.agents.mcp.server import tool_search, tool_research\n")


def _build_full_config(
    template: dict[str, Any],
    provider: str,
    doc_root: str,
    db_path: str,
    log_dir: str,
    collections_config: dict[str, Any] | None,
    use_neo4j: bool,
    neo4j_uri: str,
) -> dict[str, Any]:
    """Assemble the final ``full_config`` dict from the wizard's collected fields.

    ``provider`` is mandatory in the emitted config — factory
    construction fails without it, which was #474's headline defect
    (the wizard asked for the provider and then didn't write it).
    """
    retrieval = template.get("retrieval") or {"fusion_strategy": "bm25_primary"}
    full_config: dict[str, Any] = {
        "provider": provider,
        "paths": {"document_root": doc_root, "db_path": db_path, "log_dir": log_dir},
    }
    if collections_config:
        full_config["collections"] = collections_config
    full_config["retrieval"] = retrieval
    if use_neo4j:
        full_config["graph"] = {"enabled": True, "uri": neo4j_uri}
    return full_config


def write_config_yaml(output_path: str | Path, template_name: str, full_config: dict[str, Any]) -> Path:
    """Write the kairix YAML config file and return its path.

    The single config-writing routine shared by the terminal wizard and
    the web setup wizard backend (#474) — both surfaces emit the exact
    same file shape, so there is one place a config-format change lands.
    Silent on purpose: terminal callers print their own confirmation
    (:func:`_write_config_yaml`); the web backend must not write to
    stdout at all.
    """
    output = Path(output_path)
    with open(output, "w") as f:
        f.write("# kairix configuration — generated by kairix setup\n")
        f.write(f"# Preset: {template_name}\n\n")
        yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)
    return output


def _write_config_yaml(output_path: str, template_name: str, full_config: dict[str, Any]) -> Path:
    """Write the YAML config file, confirm on stdout, and return its path."""
    output = write_config_yaml(output_path, template_name, full_config)
    print(f"  Config saved to: {output}\n")
    return output


def _default_embed_main() -> Any:
    """Production seam — defers `kairix.core.embed.cli.main` to call time."""
    from kairix.core.embed.cli import main as embed_main

    return embed_main()


def _maybe_run_initial_index(
    ctx: SetupContext,
    file_count: int,
    *,
    embed_main_fn: Callable[[], Any] = _default_embed_main,
) -> None:
    """Offer to run the initial embed pass; never raises.

    ``embed_main_fn`` is the public DI seam — production callers leave
    the default which lazy-imports ``kairix.core.embed.cli.main``; tests
    pass a fake to drive the failure-handling branch.
    """
    print("Ready to index your documents.\n")
    if file_count <= 0:
        print("  No documents found to index. Add documents to your document store")
        print("  and run 'kairix embed' when ready.\n")
        return
    est_minutes = max(1, file_count // 1000)
    est_cost = max(1, file_count // 800)
    print("  Ready to index your documents.")
    print(f"  Estimated time: ~{est_minutes} minute{'s' if est_minutes > 1 else ''}")
    print(f"  Estimated monthly LLM cost: ~${est_cost}\n")
    # Default off in non-interactive: scripted bootstrap shouldn't trigger
    # a side-effecting embed run; the operator can run 'kairix embed' separately.
    if not prompt_yn(ctx, "Start indexing now?", default=ctx.interactive):
        print("  Skipped. Run 'kairix embed' when you're ready.\n")
        return
    print("\n  Indexing...")
    try:
        embed_main_fn()
        print("  ✓ Index built\n")
    except Exception as exc:
        logger.warning("wizard: indexing failed — %s", exc)
        print("  Indexing failed — check server logs for details.")
        print("  You can run 'kairix embed' manually later.\n")


def _run_health_check_summary() -> None:
    """Run onboarding health checks and print a one-line summary; never raises."""
    print("Running health check...")
    try:
        from kairix.platform.onboard.check import run_all_checks

        results = run_all_checks()
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        print(f"  ✓ {passed}/{total} checks passed\n")
    except Exception:
        print("  Health check skipped (run 'kairix onboard check' manually)\n")


def _print_setup_summary(output: Path, secrets_path: Path | None) -> None:
    """Print the closing 'setup complete' summary block.

    Names exactly where the config + secrets were written and the three
    commands the operator runs next, in order (#474 defect 4 — the
    epilogue is the onboarding hand-off, not a feature tour).
    """
    print("Setup complete. Your knowledge base is ready.\n")
    print(f"  Config written to:  {output}")
    if secrets_path is not None:
        print(f"  Secrets written to: {secrets_path}")
    else:
        print("  Secrets: none stored (no API key was entered)")
    print("\nNext steps:")
    print("  1. kairix embed          — build the search index from your documents")
    print("  2. kairix onboard check  — verify the deployment end to end")
    print("  3. kairix mcp serve      — connect your agents over MCP")
    print()


def _redirect_for_json_mode(json_mode: bool) -> Any:
    """In JSON mode, route narrative chatter to stderr; return the real stdout."""
    if not json_mode:
        return None
    import sys as _sys

    real_stdout = _sys.stdout
    _sys.stdout = _sys.stderr
    return real_stdout


def _emit_json_config(real_stdout: Any, full_config: dict[str, Any]) -> None:
    """Restore stdout and write the JSON config blob for scripted bootstrap."""
    import json as _json
    import sys as _sys

    if real_stdout is not None:
        _sys.stdout = real_stdout
    _sys.stdout.write(_json.dumps(full_config, indent=2) + "\n")


def run_setup(
    output_path: str = "kairix.config.yaml",
    ctx: SetupContext | None = None,
    preset: str | None = None,
    document_path: str | None = None,
    deps: WizardDeps | None = None,
) -> bool:
    """Run the setup wizard.

    Supports interactive (terminal), non-interactive (flags/defaults),
    and JSON output modes via SetupContext.

    Args:
        deps: Injectable dependencies. Tests construct
              ``WizardDeps(connection_test=fake)``; production omits the kwarg
              and the default factory wires ``probe_llm_connection``.

    Returns True if setup completed successfully.
    """
    deps = deps if deps is not None else WizardDeps()
    if ctx is None:
        ctx = SetupContext.auto_detect()

    real_stdout = _redirect_for_json_mode(ctx.json_mode)

    print("\nWelcome to kairix setup.\n")
    print("This will configure your knowledge base in a few steps.")
    print("You'll need: an LLM API key and a folder of documents.\n")

    # Step 0: use-case -> preset
    preset_key = _resolve_preset(ctx, preset)

    # Step 1: LLM backend — collect, then PERSIST. The connection test
    # runs later, against the just-written config + secrets (#474).
    print("Step 1 of 7: LLM Backend\n")
    provider_key, endpoint, api_key, embed_model = _prompt_llm_credentials(ctx)
    if provider_key == "openai" and not endpoint:
        # OpenAI-direct: the plugin takes the endpoint verbatim, so the
        # persisted value must be the real base URL, not "".
        endpoint = _OPENAI_DEFAULT_ENDPOINT
    provider_plugin = provider_plugin_name(provider_key, endpoint)
    secrets_path: Path | None = None
    if api_key:
        secrets_path = deps.persist_credentials(api_key, endpoint, embed_model)
        if secrets_path is not None:
            print(f"  ✓ Credentials stored in {secrets_path} (0600)\n")

    # Step 2: document root
    print("Step 2 of 7: Document Source\n")
    doc_root = _resolve_document_root(ctx, document_path)
    if doc_root is None:
        return False
    file_count, size_mb = count_documents(doc_root)
    print(f"\n  Found: {file_count:,} markdown files ({size_mb:.1f} MB)\n")

    # Step 3: storage location
    print("Step 3 of 7: Where to store the search index\n")
    print("  Kairix needs a place to store its search index and logs.\n")
    db_dir = _resolve_storage_dir(ctx)
    db_path = os.path.join(db_dir, "index.sqlite")
    log_dir = os.path.join(db_dir, "logs")
    print(f"  ✓ Index: {db_path}")
    print(f"  ✓ Logs: {log_dir}\n")

    # Step 4: knowledge graph
    print("Step 4 of 7: Knowledge Graph (optional)\n")
    print("  The knowledge graph tracks people, companies, and relationships")
    print("  for better search results. It requires Neo4j.")
    use_neo4j, neo4j_uri = _prompt_neo4j(ctx)

    # Step 5: search preset (purely informational)
    print("Step 5 of 7: Search Configuration\n")
    print(f"  Using '{preset_key}' preset from your use-case selection.\n")
    template = load_template(preset_key)
    template_name = template.get("name", preset_key)
    print(f"\n  Using '{template_name}' preset.\n")

    # Step 6: collections
    print("Step 6 of 7: Document Collections\n")
    print("  Collections let you organise which documents are searched.")
    print("  You can search everything, or split into groups.\n")
    collections_config = _resolve_collections(ctx, preset_key)

    # Step 7: agent integration
    print("Step 7 of 7: Agent Integration\n")
    print("  How will your agents connect to kairix?\n")
    _print_agent_instructions(ctx)

    # Build config
    full_config = _build_full_config(
        template, provider_plugin, doc_root, db_path, log_dir, collections_config, use_neo4j, neo4j_uri
    )

    if ctx.json_mode:
        # JSON mode emits the config to stdout and skips file write +
        # subsequent steps that don't make sense in scripted bootstrap.
        _emit_json_config(real_stdout, full_config)
        return True

    output = _write_config_yaml(output_path, template_name, full_config)

    # Connection test — against the just-written config + the secrets
    # read back from the bundle (#474: the probe validates what later
    # commands will actually resolve, and it can pass on a fresh
    # machine because everything it needs is now on disk).
    endpoint_rb, api_key_rb = _read_back_credentials(secrets_path, endpoint, api_key)
    if not _confirm_llm_connection(ctx, deps, provider_plugin, endpoint_rb, api_key_rb, embed_model):
        return False

    _maybe_run_initial_index(ctx, file_count, embed_main_fn=deps.embed_main)
    _run_health_check_summary()
    _print_setup_summary(output, secrets_path)
    return True
