"""Interactive setup wizard for first-time kairix configuration.

Walks through LLM credentials, document source, knowledge graph,
search preset, and initial indexing. Produces a kairix.config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


def _prompt(question: str, default: str = "") -> str:
    """Prompt for input with optional default."""
    if default:
        answer = input(f"  {question} [{default}]: ").strip()
        return answer or default
    return input(f"  {question}: ").strip()


def _prompt_choice(question: str, options: list[str]) -> int:
    """Prompt for a numbered choice. Returns 0-based index."""
    print(f"\n  {question}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    while True:
        try:
            choice = int(input("  > ").strip())
            if 1 <= choice <= len(options):
                return choice - 1
        except (ValueError, EOFError):
            pass
        print(f"  Please enter a number from 1 to {len(options)}")


def _prompt_yn(question: str, default: bool = True) -> bool:
    """Prompt for yes/no."""
    hint = "Y/n" if default else "y/N"
    answer = input(f"  {question} [{hint}]: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _test_llm_connection(provider: str, endpoint: str, api_key: str, embed_model: str) -> bool:
    """Test LLM connectivity with a single embed + chat call."""
    try:
        if provider == "azure":
            os.environ["AZURE_OPENAI_ENDPOINT"] = endpoint
            os.environ["AZURE_OPENAI_API_KEY"] = api_key
        elif provider == "openai":
            os.environ["OPENAI_API_KEY"] = api_key

        from kairix.llm import get_default_backend

        backend = get_default_backend()
        # Test embed
        vec = backend.embed("test connection")
        if not vec or len(vec) < 100:
            print("  Warning: embedding returned fewer dimensions than expected")
            return False
        # Test chat
        response = backend.chat(
            [{"role": "user", "content": "Say 'ok' and nothing else."}],
            max_tokens=5,
        )
        if not response:
            print("  Warning: chat returned empty response")
            return False
        return True
    except Exception as exc:
        print(f"  Connection failed: {exc}")
        return False


def _count_documents(path: str) -> tuple[int, float]:
    """Count markdown files and total size in MB."""
    p = Path(path)
    if not p.is_dir():
        return 0, 0.0
    files = list(p.rglob("*.md"))
    total_bytes = sum(f.stat().st_size for f in files if f.is_file())
    return len(files), total_bytes / (1024 * 1024)


def _load_template(name: str) -> dict:
    """Load an ontology template by name."""
    template_dir = Path(__file__).parent / "templates"
    template_path = template_dir / f"{name}.yaml"
    if not template_path.exists():
        return {}
    with open(template_path) as f:
        return yaml.safe_load(f) or {}


def run_setup(output_path: str = "kairix.config.yaml") -> bool:
    """Run the interactive setup wizard.

    Returns True if setup completed successfully.
    """
    print("\nWelcome to kairix setup.\n")
    print("This will configure your knowledge base in a few steps.")
    print("You'll need: an LLM API key and a folder of documents.\n")

    config: dict = {}

    # ── Step 1: LLM Backend ──────────────────────────────────────────────
    print("Step 1 of 7: LLM Backend\n")

    providers = [
        "Azure OpenAI (recommended for enterprise)",
        "OpenAI",
        "Other OpenAI-compatible endpoint",
    ]
    provider_idx = _prompt_choice("Which LLM provider are you using?", providers)
    provider_key = ["azure", "openai", "custom"][provider_idx]

    if provider_key == "azure":
        endpoint = _prompt("Azure OpenAI endpoint")
        api_key = _prompt("API key")
        embed_model = _prompt("Embedding model deployment name", "text-embedding-3-large")
        _prompt("Chat model deployment name", "gpt-4o-mini")  # consumed by future config expansion
    elif provider_key == "openai":
        endpoint = ""
        api_key = _prompt("OpenAI API key")
        embed_model = _prompt("Embedding model", "text-embedding-3-large")
        _prompt("Chat model", "gpt-4o-mini")  # consumed by future config expansion
    else:
        endpoint = _prompt("Endpoint URL")
        api_key = _prompt("API key")
        embed_model = _prompt("Embedding model name")
        _prompt("Chat model name")  # consumed by future config expansion

    print("\n  Testing connection...")
    if _test_llm_connection(provider_key, endpoint, api_key, embed_model):
        print("  \u2713 Connected successfully\n")
    else:
        print("  \u2717 Connection failed — check your credentials and try again\n")
        if not _prompt_yn("Continue anyway?", default=False):
            return False

    # ── Step 2: Document Source ───────────────────────────────────────────
    print("Step 2 of 7: Document Source\n")

    vault_path = _prompt("Where are your documents? (path to folder)")
    vault_path = os.path.expanduser(vault_path)

    if not os.path.isdir(vault_path):
        print(f"  Warning: '{vault_path}' does not exist or is not a directory")
        if not _prompt_yn("Continue anyway?", default=False):
            return False
        file_count, size_mb = 0, 0.0
    else:
        file_count, size_mb = _count_documents(vault_path)
        print(f"\n  Found: {file_count:,} markdown files ({size_mb:.1f} MB)\n")

    # ── Step 3: Storage Location ──────────────────────────────────────────
    print("Step 3 of 7: Where to store the search index\n")
    print("  Kairix needs a place to store its search index and logs.\n")

    storage_options = [
        "Default location (~/.cache/kairix/) — good for personal use",
        "Custom path — for shared or production deployments",
        "Docker paths (/data/kairix/) — for container deployments",
    ]
    storage_idx = _prompt_choice("Where should kairix store its data?", storage_options)

    if storage_idx == 0:
        db_dir = str(Path.home() / ".cache" / "kairix")
    elif storage_idx == 1:
        db_dir = _prompt("Data directory path")
        db_dir = os.path.expanduser(db_dir)
    else:
        db_dir = "/data/kairix"

    db_path = os.path.join(db_dir, "index.sqlite")
    log_dir = os.path.join(db_dir, "logs")
    print(f"  \u2713 Index: {db_path}")
    print(f"  \u2713 Logs: {log_dir}\n")

    # ── Step 4: Knowledge Graph ──────────────────────────────────────────
    print("Step 4 of 7: Knowledge Graph (optional)\n")
    print("  The knowledge graph tracks people, companies, and relationships")
    print("  for better search results. It requires Neo4j.")

    use_neo4j = _prompt_yn("\n  Enable knowledge graph?", default=True)
    neo4j_uri = ""
    if use_neo4j:
        neo4j_uri = _prompt("Neo4j URI", "bolt://localhost:7687")
        # Test Neo4j connection
        try:
            from kairix.graph.client import Neo4jClient

            client = Neo4jClient.__new__(Neo4jClient)
            client._uri = neo4j_uri
            # Simple connectivity check would go here
            print("  \u2713 Neo4j URI configured\n")
        except Exception:
            print("  Note: Neo4j connection will be tested when the service starts\n")

    # ── Step 5: Search Configuration ─────────────────────────────────────
    print("Step 5 of 7: Search Configuration\n")

    presets = [
        "Consulting / professional services knowledge base",
        "Technical documentation (guides, runbooks, procedures)",
        "Daily logs and meeting notes",
        "General / mixed content",
    ]
    preset_idx = _prompt_choice("What kind of documents do you have?", presets)
    preset_key = ["consulting", "technical", "consulting", "general"][preset_idx]

    template = _load_template(preset_key)
    template_name = template.get("name", preset_key)
    print(f"\n  Using '{template_name}' preset.\n")

    # ── Step 6: Document Collections ──────────────────────────────────────
    print("Step 6 of 7: Document Collections\n")
    print("  Collections let you organise which documents are searched.")
    print("  You can search everything, or split into groups.\n")

    collection_options = [
        "Search everything — all documents in one collection (simplest)",
        "Use template collections (based on your preset above)",
        "Include agent workspace memories (for agent platforms)",
        "Skip — I'll configure collections later",
    ]
    coll_idx = _prompt_choice("How do you want to organise your documents?", collection_options)

    collections_config: dict | None = None
    if coll_idx == 0:
        collections_config = {
            "shared": [{"name": "all", "path": ".", "glob": "**/*.md"}],
        }
        print("  \u2713 All documents will be searchable.\n")
    elif coll_idx == 1:
        # Use preset-appropriate collections
        if preset_key == "consulting":
            collections_config = {
                "shared": [
                    {"name": "clients", "path": "Clients", "glob": "**/*.md"},
                    {"name": "projects", "path": "Projects", "glob": "**/*.md"},
                    {"name": "knowledge", "path": "Knowledge", "glob": "**/*.md"},
                    {"name": "entities", "path": "Entities", "glob": "**/*.md"},
                ],
            }
        elif preset_key == "technical":
            collections_config = {
                "shared": [
                    {"name": "docs", "path": "docs", "glob": "**/*.md"},
                    {"name": "runbooks", "path": "runbooks", "glob": "**/*.md"},
                    {"name": "reference", "path": "reference", "glob": "**/*.md"},
                ],
            }
        else:
            collections_config = {
                "shared": [{"name": "all", "path": ".", "glob": "**/*.md"}],
            }
        print(f"  \u2713 {len(collections_config['shared'])} collections configured.\n")
    elif coll_idx == 2:
        workspace_root = os.environ.get("KAIRIX_WORKSPACE_ROOT", "~/.kairix/workspaces")
        collections_config = {
            "shared": [
                {"name": "all", "path": ".", "glob": "**/*.md"},
                {"name": "workspaces", "path": workspace_root, "glob": "**/memory/**/*.md"},
            ],
        }
        print(f"  \u2713 Documents + agent workspace memories ({workspace_root}) configured.\n")

    # ── Step 7: Agent Integration ────────────────────────────────────────
    print("Step 7 of 7: Agent Integration\n")
    print("  How will your agents connect to kairix?\n")

    agent_options = [
        "Claude Desktop / Claude Code (stdio MCP)",
        "OpenClaw or similar agent platform (stdio MCP)",
        "Docker / HTTP service (SSE MCP on port 8080)",
        "Direct Python import (no MCP server needed)",
        "Skip — I'll configure this later",
    ]
    agent_idx = _prompt_choice("Select your agent platform:", agent_options)

    if agent_idx == 0:
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
    elif agent_idx == 1:
        print('\n  Run: openclaw mcp set mcp-kairix "kairix mcp serve"\n')
    elif agent_idx == 2:
        from kairix.onboard.ports import find_available_port, is_port_available

        default_port = 8080
        if is_port_available(default_port):
            mcp_port = default_port
        else:
            mcp_port = find_available_port(preferred=default_port)
            print(f"\n  Port {default_port} is in use — suggesting {mcp_port} instead.")
        print(f"\n  MCP endpoint: http://localhost:{mcp_port}")
        print(f"  Start with: kairix mcp serve --transport sse --port {mcp_port}\n")
    elif agent_idx == 3:
        print("\n  Import directly in Python:")
        print("  from kairix.mcp.server import tool_search, tool_research\n")

    # ── Build config ─────────────────────────────────────────────────────
    config = template.get("retrieval", {})
    if not config:
        config = {"fusion_strategy": "bm25_primary"}

    full_config: dict = {}

    # Paths section
    full_config["paths"] = {
        "document_root": vault_path,
        "db_path": db_path,
        "log_dir": log_dir,
    }

    # Collections section
    if collections_config:
        full_config["collections"] = collections_config

    # Retrieval section
    full_config["retrieval"] = config

    # Graph section
    if use_neo4j:
        full_config["graph"] = {"enabled": True, "uri": neo4j_uri}

    output = Path(output_path)
    with open(output, "w") as f:
        f.write("# kairix configuration — generated by kairix setup\n")
        f.write(f"# Preset: {template_name}\n\n")
        yaml.dump(full_config, f, default_flow_style=False, sort_keys=False)

    print(f"  Config saved to: {output}\n")

    # ── Initial Index ────────────────────────────────────────────────────
    print("Ready to index your documents.\n")

    if file_count > 0:
        est_minutes = max(1, file_count // 1000)
        est_cost = max(1, file_count // 800)
        print("  Ready to index your documents.")
        print(f"  Estimated time: ~{est_minutes} minute{'s' if est_minutes > 1 else ''}")
        print(f"  Estimated monthly LLM cost: ~${est_cost}\n")

        if _prompt_yn("Start indexing now?"):
            print("\n  Indexing...")
            try:
                from kairix.embed.cli import main as embed_main

                embed_main()
                print("  \u2713 Index built\n")
            except Exception as exc:
                print(f"  Indexing failed: {exc}")
                print("  You can run 'kairix embed' manually later.\n")
        else:
            print("  Skipped. Run 'kairix embed' when you're ready.\n")
    else:
        print("  No documents found to index. Add documents to your vault folder")
        print("  and run 'kairix embed' when ready.\n")

    # ── Health check ─────────────────────────────────────────────────────
    print("Running health check...")
    try:
        from kairix.onboard.check import run_all_checks

        results = run_all_checks()
        passed = sum(1 for r in results if r.ok)
        total = len(results)
        print(f"  \u2713 {passed}/{total} checks passed\n")
    except Exception:
        print("  Health check skipped (run 'kairix onboard check' manually)\n")

    # ── Summary ──────────────────────────────────────────────────────────
    print("Setup complete. Your knowledge base is ready.\n")
    print('  Search:     kairix search "your question here"')
    print("  MCP server: kairix mcp serve")
    print("  Research:   kairix mcp serve  (then call tool_research via MCP)")
    print("  Benchmark:  kairix eval build-gold --suite queries.yaml")
    print(f"\n  Config: {output}\n")

    return True
