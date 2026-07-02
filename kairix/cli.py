"""
kairix — private knowledge retrieval for AI agents and teams.

Subcommands:
  bootstrap   Agent orientation envelope: role, board, recent memory, goals, health
  features    Inspect registered feature flags (status + effective values)
  embed       Embed documents into the kairix vector index
  search      Hybrid search: BM25 + vector via RRF
  expand      Pull a search hit's neighbouring chunks (preceding/following) within a token budget
  entity      Entity management: suggest (NER), validate (Wikidata), audit, purge
  curator     Curator agent: entity health monitoring and enrichment (CA-1)
  contradict  Contradiction detection: check new content against existing knowledge
  store       Document store operations: crawl entities into Neo4j, health check
  mcp         MCP server: expose search/entity/prep/timeline as MCP tools
  onboard     Deployment diagnostics and agent onboarding (check, guide, verify)
  doctor      Re-validate configured agent scopes against disk state (drift detection)
  timeline    Temporal query rewriting + date-aware retrieval
  summarise   L0/L1 tiered context generation
  classify    Auto-classify memory writes
  brief       Session briefing synthesis
  prep        Tiered L0/L1 context summary for a topic
  research    Iterative research over the knowledge store with LLM synthesis
  usage-guide Read the kairix agent usage guide (full text or topic-filtered)
  benchmark   Run retrieval quality benchmark (concurrent latency + soak modes folded in here — formerly `kairix probe` / `kairix soak`)
  probe-config  Probe the configured provider for health + tuning recommendations
  mcp-calls   Inspect the mcp_call_log per-MCP-tool-call observability table
  caches      Inspect the in-memory cache stats (prep summary cache, brief output cache)
  slo         Perf & affordance SLO harness: cold/warm/concurrency latency + fact-recall + breadcrumb completeness across brief/remember/recall/search
  warm        Pre-load caches + pay factory-init costs (run at container start, before /healthz/ready=200)
  wikilinks   Inject [[wikilinks]] on first mention in agent-written document store files
  reference-library  Reference library: install entities, check status, run extraction
  eval        Evaluation harness: gold suite build, judge, sweep, monitor, gate (Plan B-parity D3: scores route through the same SearchPipeline kairix prep uses; --legacy-direct bypasses the pipeline)
  setup       First-time onboarding wizard for credentials and paths
  worker      Background worker: run loop, pause/resume operator controls
  config      Validate kairix.config.yaml against the schema and print errors
  ingest-chat Ingest JSONL chat transcripts into the document + fact stores
  facts-about Look up what kairix knows about an entity (facts + entity summary)
  remember    Save a memory for an agent (dated markdown file + immediate BM25 index)
  recommend   Recommend which kairix tool or local skill fits a described task (ranked)
  cc-pair     Operator surface over topology_cc_pairs (list/create/pause/resume/delete)
  dead-letter Operator triage view over the connector_deadletter table (status)
  secrets     Canonical credential naming: verify resolution + set (persist a secret into the operator bundle)
  connect     Operator-only: capture OAuth2 tokens for connectors (Google + GitHub App)
  maintenance Operator surface for ad-hoc maintenance tasks (analyze: refresh planner stats)
  init        Self-installer: lay down FHS/XDG dir tree, config template, and systemd unit (--system / --user / verify)
  uninstall   Remove the kairix install layout (--system / --user; --no-keep-data also deletes data dir)

See KAIRIX-ARCHITECTURE.md for architecture, ADRs, and roadmap.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from kairix.agents.mcp.server import CAPABILITIES_CATALOG, Capability


def _default_mcp_dispatch(subcommand: str, argv: list[str]) -> int | None:
    """Production default for ``CliDeps.mcp_dispatch``.

    Lazy-imports the dispatcher so the CLI doesn't pay the cost when
    routing isn't reachable. Returns ``None`` (in-process fall-through)
    when the optional ``mcp`` extra isn't installed — bit-identical to
    today's behaviour.
    """
    from kairix.agents.mcp.client_dispatcher import try_dispatch_via_mcp

    return try_dispatch_via_mcp(subcommand, argv)


@dataclass(frozen=True)
class CliDeps:
    """Injection seam for :func:`main`.

    Production callers leave it ``None`` and the dispatcher constructs
    the default Deps which wires the real :func:`try_dispatch_via_mcp`.
    Tests pass a fake callable so they can drive the wiring without
    monkey-patching the dispatcher module.

    F6-clean: lives on a Deps dataclass (canonical shape per
    ``WorkerDeps``), not as a ``*_fn=None`` test-only kwarg on
    :func:`main`.
    """

    mcp_dispatch: Callable[[str, list[str]], int | None] = field(default=_default_mcp_dispatch)


# ---------------------------------------------------------------------------
# CLI dispatch table — DERIVED from the capability catalogue, not hand-listed.
# ---------------------------------------------------------------------------
#
# ``_CLI_HANDLERS`` is the ONE place a subcommand's target module lives — the
# catalogue (:data:`CAPABILITIES_CATALOG`) owns the agent-facing surface but
# carries no import wiring. ``COMMANDS`` — the table :func:`main` dispatches
# through — is DERIVED from it at import by :func:`_derive_commands`: every key
# must be backed either by a ``CAPABILITIES_CATALOG`` row (an agent-facing
# capability, keyed by that row's ``cli`` field) or by an explicit
# ``_INFRA_SUBCOMMANDS`` entry (an operator/infra command that is deliberately
# not an agent capability). A handler that is neither raises at import, so a
# subcommand can't silently drift from the catalogue and a retired alias — the
# old ``vault`` → ``store`` alias — can't survive. Lazy imports in :func:`main`
# keep startup fast: only the selected command's module is imported at dispatch.
#
# Command → (module_path, function_name, accepts_args).
_CLI_HANDLERS: dict[str, tuple[str, str, bool]] = {
    "bootstrap": ("kairix.bootstrap_cli", "main", True),
    "embed": ("kairix.core.embed.cli", "main", True),
    "entity": ("kairix.knowledge.entities.cli", "main", True),
    "curator": ("kairix.agents.curator.cli", "main", True),
    "search": ("kairix.core.search.cli", "main", True),
    # F45-feature: tests/bdd/features/cli_expand.feature
    "expand": ("kairix.use_cases.expand", "main", True),
    "benchmark": ("kairix.quality.benchmark.cli", "main", True),
    "probe-config": ("kairix.quality.probe.config_cli", "main", True),
    "mcp-calls": ("kairix.quality.probe.mcp_calls_cli", "main", True),
    "caches": ("kairix.quality.probe.caches_cli", "main", True),
    # F45-feature: tests/bdd/features/cli_slo.feature
    "slo": ("kairix.quality.probe.slo_cli", "main", True),
    "warm": ("kairix.platform.warm.cli", "main", True),
    "summarise": ("kairix.knowledge.summaries.cli", "main", True),
    "timeline": ("kairix.core.temporal.cli", "main", True),
    "wikilinks": ("kairix.knowledge.wikilinks.cli", "main", True),
    "classify": ("kairix.core.classify.cli", "main", True),
    "brief": ("kairix.agents.briefing.cli", "main", True),
    "prep": ("kairix.agents.prep.cli", "main", True),
    "research": ("kairix.agents.research.cli", "main", True),
    "usage-guide": ("kairix.agents.usage_guide.cli", "main", True),
    "contradict": ("kairix.knowledge.contradict.cli", "main", True),
    "store": ("kairix.knowledge.store.cli", "main", True),
    "mcp": ("kairix.agents.mcp.cli", "main", True),
    "onboard": ("kairix.platform.onboard.cli", "main", True),
    # F45-feature: tests/bdd/features/cli_doctor.feature
    "doctor": ("kairix.agents.onboarding.doctor_cli", "main", True),
    "eval": ("kairix.use_cases.eval_suite", "main", True),
    "reference-library": ("kairix.knowledge.reflib.cli", "main", True),
    "setup": ("kairix.platform.setup.cli", "main", True),
    "worker": ("kairix.worker_cli", "main", True),
    "config": ("kairix.core.search.config_validator", "main", True),
    "ingest-chat": ("kairix.use_cases.ingest_chat", "main", True),
    # F45-feature: tests/bdd/features/cli_facts_about.feature
    "facts-about": ("kairix.agents.mcp.tools.facts_about_cli", "main", True),
    # F45-feature: tests/bdd/features/cli_remember.feature
    "remember": ("kairix.use_cases.remember", "main", True),
    # F45-feature: tests/bdd/features/cli_recommend.feature
    "recommend": ("kairix.use_cases.recommend", "main", True),
    "features": ("kairix.core.features.cli", "main", True),
    "cc-pair": ("kairix.core.connectors.cc_pair_cli", "main", True),
    "dead-letter": ("kairix.dead_letter_cli", "main", True),
    "secrets": ("kairix.secrets.cli", "main", True),
    "connect": ("kairix.connect.cli", "main", True),
    "maintenance": ("kairix.core.maintenance.cli", "main", True),
    # F45-feature: tests/bdd/features/install_user_mode.feature
    "init": ("kairix.install.init_cli", "main", True),
    # F45-feature: tests/bdd/features/install_system_mode.feature
    "uninstall": ("kairix.install.uninstall_cli", "main", True),
}

# Operator / infrastructure subcommands that are intentionally NOT agent
# capabilities, so they carry no ``CAPABILITIES_CATALOG`` row. Each is a
# deliberate human-facing command; anything listed here MUST have
# ``_CLI_HANDLERS`` wiring, and any dispatchable subcommand that is neither
# catalogue-backed nor listed here is rejected by :func:`_derive_commands`.
_INFRA_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "mcp",  # MCP server transport
        "setup",  # first-time onboarding wizard
        "config",  # config-schema validator
        "summarise",  # L0/L1 tiered context generation
        "classify",  # auto-classify memory writes
        "wikilinks",  # inject [[wikilinks]] on first mention
        "curator",  # curator agent (entity health monitoring)
        "eval",  # evaluation harness
        "reference-library",  # reference library installer
        "init",  # self-installer (FHS/XDG dir tree)
        "uninstall",  # remove the install layout
        "connect",  # operator-only OAuth2 token capture
        "mcp-calls",  # mcp_call_log observability inspector
        "slo",  # perf & affordance SLO harness
        # The catalogue spells this capability "kairix facts about"; the shipped
        # command is the hyphenated "facts-about", so it dispatches via infra.
        "facts-about",
    }
)


def _cli_subcommand(cli_invocation: str) -> str | None:
    """Return the top-level ``kairix <sub>`` token of a catalogue ``cli`` field.

    Catalogue rows whose ``cli`` is not a ``kairix …`` invocation — the probe /
    soak escalation stubs run via ``python -c '…'`` — carry no dispatchable
    subcommand and return ``None``.
    """
    parts = cli_invocation.split()
    if len(parts) >= 2 and parts[0] == "kairix":
        return parts[1]
    return None


def _derive_commands(
    catalogue: tuple[Capability, ...],
    handlers: dict[str, tuple[str, str, bool]],
    infra: frozenset[str],
) -> dict[str, tuple[str, str, bool]]:
    """Build the dispatch table from the capability catalogue + infra list.

    Agent-facing subcommands come from ``catalogue`` (each row's ``cli`` field
    names the ``kairix <sub>`` command); operator/infra subcommands come from
    ``infra``. Every resulting key is wired to its handler in ``handlers``. Two
    invariants raise at import so the shipped table can't drift from the
    catalogue:

    * a ``handlers`` entry that is neither catalogue-backed nor infra-declared
      is a stale/aliased subcommand — this is what keeps the retired ``vault``
      alias from surviving; and
    * an ``infra`` subcommand with no ``handlers`` wiring is a config gap.

    A catalogue ``cli`` that names no shipped command (the MCP-only
    ``capabilities`` surface, or ``facts about`` — shipped as ``facts-about``
    through ``infra``) is simply not dispatched here.
    """
    catalogue_subs: set[str] = set()
    for cap in catalogue:
        sub = _cli_subcommand(cap.cli)
        if sub is not None:
            catalogue_subs.add(sub)

    commands: dict[str, tuple[str, str, bool]] = {}
    for sub in sorted(catalogue_subs & handlers.keys()):
        commands[sub] = handlers[sub]
    for sub in sorted(infra):
        wiring = handlers.get(sub)
        if wiring is None:
            raise RuntimeError(
                f"_INFRA_SUBCOMMANDS names {sub!r} but _CLI_HANDLERS has no wiring for it. "
                f"fix: add a ('module', 'main', True) row to _CLI_HANDLERS. "
                f"next: or drop {sub!r} from _INFRA_SUBCOMMANDS."
            )
        commands[sub] = wiring

    orphans = handlers.keys() - commands.keys()
    if orphans:
        raise RuntimeError(
            f"CLI handler(s) {sorted(orphans)} are neither backed by a CAPABILITIES_CATALOG "
            f"capability nor declared in _INFRA_SUBCOMMANDS, so COMMANDS can't derive them. "
            f"fix: add a catalogue row in kairix/agents/mcp/server.py for an agent-facing command, "
            f"or add the subcommand to _INFRA_SUBCOMMANDS for an operator-only one. "
            f"next: the retired `vault` alias is intentionally gone — use `store`."
        )
    return commands


# Derived at import: keys come from the catalogue + the infra allow-list, so a
# subcommand can't drift from the catalogue (and the old `vault` alias is gone).
COMMANDS: dict[str, tuple[str, str, bool]] = _derive_commands(CAPABILITIES_CATALOG, _CLI_HANDLERS, _INFRA_SUBCOMMANDS)


def main(
    *,
    commands: dict[str, tuple[str, str, bool]] | None = None,
    deps: CliDeps | None = None,
) -> None:
    """Top-level ``kairix`` CLI dispatcher.

    The ``commands`` kwarg is the public DI seam: production callers leave
    it ``None`` and the dispatcher reads :data:`COMMANDS` from this module;
    tests pass a synthetic dispatch table to drive the routing logic
    without monkey-patching the module attribute.

    The ``deps`` kwarg is the #411 DI seam — leave it ``None`` and the
    real MCP dispatcher is used; tests pass a ``CliDeps`` carrying a
    fake ``mcp_dispatch`` callable to drive the wiring without patching
    the dispatcher module.
    """
    table = commands if commands is not None else COMMANDS
    effective_deps = deps if deps is not None else CliDeps()

    # Hydrate the secrets bundle into os.environ ONCE per CLI invocation,
    # before any subcommand runs. After this call, every SecretsLoader
    # (which reads os.environ live) sees the bundle's canonical env vars.
    # See kairix/secrets/bootstrap.py for the why — closes the
    # 2026-06-01 production class of bug where caller code had to
    # remember to hydrate before resolving secrets.
    from kairix.secrets.bootstrap import bootstrap_secrets

    bootstrap_secrets()

    # KAIRIX_TRACE=1 opts the operator into structured diagnostic logging
    # (D4 — Plan B-parity remediation). The trace lines emit at INFO via
    # ``logger.info``, so the CLI needs a handler installed; without this
    # block ``kairix prep`` runs silently and the documented diagnostic
    # path is a no-op for end users.
    from kairix.paths import trace_enabled as _trace_enabled

    if _trace_enabled():
        import logging as _logging

        _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print(__doc__)
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    cmd = sys.argv[1]

    if cmd in ("--version", "-V", "version"):
        from kairix import __version__

        print(f"kairix {__version__}")
        sys.exit(0)

    entry = table.get(cmd)
    if entry is None:
        print(f"Unknown command: {cmd}\n{__doc__}", file=sys.stderr)
        sys.exit(1)

    # #411 — opt-in shortcut: when a warm MCP server is reachable, route
    # the subcommand through it instead of paying the cold-start cost
    # in-process. The dispatcher returns None (and we fall through to
    # in-process) when: routing is disabled, the subcommand has no MCP
    # equivalent, the user didn't pass --json, args don't translate, or
    # the server isn't responsive within the 100ms detection budget.
    # Bit-identical fallback to today's behaviour in every other case.
    mcp_exit_code = effective_deps.mcp_dispatch(cmd, sys.argv[2:])
    if mcp_exit_code is not None:
        sys.exit(mcp_exit_code)

    module_path, func_name, accepts_args = entry
    import importlib

    mod = importlib.import_module(module_path)
    fn = getattr(mod, func_name)

    if accepts_args:
        result = fn(sys.argv[2:])
        if result is not None:
            sys.exit(result)
    else:
        fn()


if __name__ == "__main__":
    main()
