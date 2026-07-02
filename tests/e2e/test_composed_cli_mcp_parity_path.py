"""E2E capstone: CLI ↔ MCP cross-entry-point consistency + latency (PLA-320, W3).

The Agent-Interface Consistency wave consolidated every capability so a single
``run_<name>`` use case backs BOTH the ``kairix`` CLI and the MCP tool. This
harness is the capstone proof that the two surfaces stay consistent —
behaviour, the ``source_uri`` breadcrumb (F97/F98), and per-surface latency —
and that the proof is DERIVED FROM THE CATALOGUE so it cannot silently drift.

Two tiers, both keyed on :func:`kairix.agents.mcp.server.agent_facing`:

1. **Catalogue drift-lock** (parametrised over the WHOLE ``agent_facing()``
   surface). For every agent-facing capability we prove both entry points are
   wired: the MCP tool is registered by :func:`build_server` and the CLI
   subcommand resolves in :data:`kairix.cli.COMMANDS` (unless the capability is
   a documented MCP-only surface). A new capability is auto-enumerated here;
   a removed one drops out of the parametrisation, so neither can leave a
   stale test.

2. **Cross-entry-point parity** (parametrised over a curated set of drivers).
   Each driver runs the capability through BOTH real entry points against ONE
   hermetic composed store and asserts (a) both surfaces succeed, (b) the
   underlying data is equivalent — we compare the data, not byte-identical
   text, because the CLI renders/serialises and MCP returns a dict, (c) for the
   breadcrumb-bearing surfaces the resolvable ``source_uri`` is present and
   EQUAL across surfaces (the F97/F98 locator an agent cites/expands from), and
   (d) each surface completes under a documented latency ceiling.

   The CLI entry point runs as a real
   ``subprocess.run([sys.executable, "-m", "kairix.cli", <sub>, ...])`` for the
   four capabilities that expose an offline CLI seam (``expand`` /
   ``facts-about`` via ``--db-path``, ``bootstrap`` via ``--document-root``,
   ``usage-guide`` via the bundled guide). ``search`` has no offline path in a
   subprocess: with no provider configured the factory hard-raises
   ("kairix.config.yaml is missing the required 'provider:' field",
   ``kairix.core.factory._build_embedding_service``) and every installed
   provider plugin needs a live network endpoint + key, so a hermetic
   ``kairix search`` subprocess is impossible. ``search`` therefore drives its
   real CLI entry point (``kairix.core.search.cli.main``) in-process over an
   injected fake-provider pipeline — the same composed store the MCP handler
   reads — so the flagship retrieval breadcrumb parity is still proven offline.

Hermeticity (CRITICAL): every store / index / config lives under the pytest
``tmp_path``. Subprocess CLI calls redirect ``HOME`` + every ``XDG_*`` base dir
into ``tmp_path`` and pin the seeded index via the explicit ``--db-path`` /
``--document-root`` seams (never a ``KAIRIX_*`` env var), so nothing touches the
real data dir (``~/.local/share/kairix``, ``vectors.usearch``, …). The in-process
surfaces read the same tmp store through injected ``deps=`` / ``paths=`` seams.
The autouse ``_reset_search_pipeline_cache`` fixture (``tests/e2e/conftest.py``)
keeps the factory cache from leaking a tmp-bound pipeline between tests.

F48 sibling: lives under ``tests/e2e/`` with ``@pytest.mark.e2e``; runs in CI
Stage 4.5 under ``pytest -m e2e``.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kairix.agents.mcp.server import CAPABILITIES_TOOL_NAME, Capability, agent_facing, build_server
from kairix.agents.mcp.tools.facts_about import tool_facts_about
from kairix.agents.mcp.tools.orient import tool_bootstrap, tool_usage_guide
from kairix.agents.mcp.tools.retrieval import tool_expand, tool_search
from kairix.cli import COMMANDS
from kairix.core.db.repository import SQLiteDocumentRepository
from kairix.core.db.schema import create_schema
from kairix.core.facts import SQLiteFactStore, StoredFactRecord
from kairix.core.factory import RERANK_DISABLED, FactoryDeps, build_search_pipeline
from kairix.core.search.config import RetrievalConfig
from kairix.core.search.scope import Scope
from kairix.use_cases.bootstrap import BootstrapDeps
from kairix.use_cases.expand import ExpandDeps
from kairix.use_cases.search import SearchDeps
from tests.fakes import FakePaths, FakeProvider, FakeProviderRegistry, FakeVectorRepository

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Fixture constants — distinctive tokens so BM25 / FTS match exactly the seed.
# ---------------------------------------------------------------------------

_TERM = "wombat-relay-cascade"
_SOURCE_URI = "sharepoint://site/parity-capstone-doc"
_CHUNK_COUNT = 4
_COLLECTION = "team-notes"

_AGENT = "agent-alpha"  # F32 — generic agent name, never a real person.
_NAMESPACE = "engagement-alpha"
_FACT_SOURCE_URI = "sharepoint://site/parity-capstone-fact"

# Envelope key + CLI-flag constants (F17 — each appears 3+ times).
_K_SOURCE_URI = "source_uri"
_K_ERROR = "error"
_FLAG_JSON = "--json"
_FLAG_DB_PATH = "--db-path"

# Documented MCP-only agent-facing surfaces — no ``kairix <sub>`` CLI dispatch.
# ``capabilities`` is the introspection tool (MCP-only by design); ``probe_search``
# is reachable via the Python API only (its ``cli`` names ``python -c '…'``, not a
# shipped subcommand). Any OTHER agent-facing capability MUST resolve a CLI
# subcommand — the drift-lock below fails if a new one slips through unwired.
_MCP_ONLY_CAPABILITIES = frozenset({CAPABILITIES_TOOL_NAME, "probe_search"})

# Latency ceilings. The subprocess ceiling is dominated by interpreter + import
# startup, so it is a generous "did not hang / did not fall through to a network
# provider" guard rather than a tight budget. The in-process ceiling is the
# meaningful per-call perf-regression guard Dan asked for.
_CLI_SUBPROCESS_CEILING_MS = 30_000.0
_INPROCESS_CEILING_MS = 5_000.0

_SUBPROCESS_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Seeding — ONE composed store under tmp_path (real SQLite + FTS + fact store).
# ---------------------------------------------------------------------------


def _seed_chunk_document(db_path: Path) -> None:
    """Seed a real chunked document (``<source_uri>#<seq>`` rows) + its FTS index.

    Mirrors the worker's ``_SqliteChunkWriter`` row shape so both the search
    pipeline (BM25 over FTS) and expand (``get_by_path`` / ``list_chunk_seqs``)
    resolve the same document, and every hit carries the canonical
    ``source_uri`` breadcrumb (``documents.source_uri``).
    """
    db = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        create_schema(db)
        for seq in range(_CHUNK_COUNT):
            chunk_hash = f"parity-hash-{seq}"
            db.execute(
                "INSERT INTO documents (collection, path, hash, source_uri, sensitivity, active) "
                "VALUES (?, ?, ?, ?, 'public', 1)",
                (_COLLECTION, f"{_SOURCE_URI}#{seq}", chunk_hash, _SOURCE_URI),
            )
            db.execute(
                "INSERT INTO content (hash, doc) VALUES (?, ?)",
                (chunk_hash, f"{_TERM} chunk number {seq} of the parity capstone document"),
            )
        db.execute("DELETE FROM documents_fts")
        db.execute(
            """
            INSERT INTO documents_fts (rowid, filepath, title, doc)
            SELECT d.id, d.path, d.title, c.doc
            FROM documents d
            JOIN content c ON c.hash = d.hash
            WHERE d.active = 1
            """
        )
        db.commit()
    finally:
        db.close()


def _seed_fact(db_path: Path) -> None:
    """Seed one recallable fact carrying an explicit resolvable ``source_uri``.

    ``facts_about(_AGENT)`` recalls it via the fact store's FTS leg; both the CLI
    (``--db-path``) and MCP (``paths=``) surfaces read the SAME row, so the
    resolved breadcrumb is identical.
    """
    record = StoredFactRecord(
        id=StoredFactRecord.mint_id(entity=_AGENT, attribute="rollout-cadence", source_turn_ids=("t1",)),
        entity=_AGENT,
        attribute="rollout-cadence",
        value=f"prefers the {_TERM} cadence",
        confidence=0.9,
        source_turn_ids=("t1",),
        extracted_at="2026-07-01T00:00:00Z",
        superseded_by=None,
        namespace=_NAMESPACE,
        conversation_id="conv-parity-capstone",
        source_uri=_FACT_SOURCE_URI,
    )
    SQLiteFactStore(db_path=db_path).add(record)


def _seed_bootstrap_root(document_root: Path) -> None:
    """Seed the agent-knowledge surface so bootstrap returns a non-empty envelope."""
    agent_dir = document_root / "04-Agent-Knowledge" / _AGENT
    agent_dir.mkdir(parents=True)
    (agent_dir / "Board.md").write_text(f"# Board\n\nPriority: ship the {_TERM} rollout.\n", encoding="utf-8")
    (agent_dir / "Goals.md").write_text("## Goals\n\n- land PLA-320\n- prove CLI/MCP parity\n", encoding="utf-8")
    (agent_dir / "Role.md").write_text("Builder — parity capstone owner\n", encoding="utf-8")


@dataclass(frozen=True)
class _ParityStore:
    """Handles to the ONE hermetic composed store shared by both entry points."""

    tmp_path: Path
    db_path: Path
    document_root: Path
    fake_paths: Any
    search_deps: SearchDeps

    def subprocess_env(self) -> dict[str, str]:
        """A hermetic env: redirect HOME + every XDG base into tmp; no KAIRIX_* var.

        Nothing the subprocess resolves (config, secrets, data dir, cache) can
        escape ``tmp_path``. The seeded index is pinned separately via each
        subcommand's explicit ``--db-path`` / ``--document-root`` seam.
        """
        env = dict(os.environ)
        env["HOME"] = str(self.tmp_path)
        env["XDG_CONFIG_HOME"] = str(self.tmp_path / "config")
        env["XDG_DATA_HOME"] = str(self.tmp_path / "data")
        env["XDG_CACHE_HOME"] = str(self.tmp_path / "cache")
        env["XDG_RUNTIME_DIR"] = str(self.tmp_path / "runtime")
        for key in list(env):
            if key.startswith("KAIRIX_"):
                del env[key]
        return env


def _make_search_deps(db_path: Path, document_root: Path, tmp_path: Path) -> SearchDeps:
    """Build a ``SearchDeps`` whose ``search_fn`` is an offline composed pipeline.

    The pipeline is factory-composed over the tmp SQLite index with a
    ``FakeProvider`` embedding backend (no network), so both the CLI and MCP
    search surfaces run the SAME real ``SearchPipeline`` — the composition
    discipline F46/F47 requires — while staying hermetic.
    """
    paths = FakePaths(
        document_root=document_root,
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    registry = FakeProviderRegistry({"fake": FakeProvider(name="fake", vector=[0.1] * 1536, dim=1536)})
    # RERANK_DISABLED wires reranker=None (a structural no-op). The production
    # cross-encoder reranker lazy-loads a HuggingFace BERT model on first search
    # (network + tens of seconds), which would break hermeticity and the latency
    # budget. Both search surfaces share this one pipeline, so skipping rerank
    # removes the model load without affecting CLI↔MCP parity (both get the same
    # non-reranked BM25 order).
    # vec_repo_override keeps the vector backend off the real usearch index path
    # (kairix.paths.vec_index_path resolves the real data dir even under FakePaths),
    # so the pipeline is fully tmp-isolated; BM25 over the tmp FTS index carries
    # the breadcrumb this parity check reads.
    pipeline = build_search_pipeline(
        config=RetrievalConfig(provider="fake"),
        registry=registry,
        paths=paths,
        deps=FactoryDeps(reranker_override=RERANK_DISABLED, vec_repo_override=FakeVectorRepository()),
    )

    def _search_fn(
        query: str,
        budget: int,
        scope: Scope,
        agent: str | None,
        collections: list[str] | None = None,
        intent: Any = None,
        max_tier: str = "L2",
    ) -> Any:
        return pipeline.search(
            query=query,
            budget=budget,
            scope=scope,
            agent=agent,
            collections=collections,
            intent=intent,
            max_tier=max_tier,
        )

    return SearchDeps(search_fn=_search_fn)


@pytest.fixture
def parity_store(tmp_path: Path) -> _ParityStore:
    """Seed ONE composed store (chunk doc + fact + bootstrap surface) under tmp_path."""
    db_path = tmp_path / "index.sqlite"
    document_root = tmp_path / "documents"
    document_root.mkdir()

    _seed_chunk_document(db_path)
    _seed_fact(db_path)
    _seed_bootstrap_root(document_root)

    fake_paths = FakePaths(
        document_root=document_root,
        db_path=db_path,
        log_dir=tmp_path / "logs",
        workspace_root=tmp_path / "workspaces",
    )
    return _ParityStore(
        tmp_path=tmp_path,
        db_path=db_path,
        document_root=document_root,
        fake_paths=fake_paths,
        search_deps=_make_search_deps(db_path, document_root, tmp_path),
    )


# ---------------------------------------------------------------------------
# CLI runners — real subprocess (offline seams) or in-process real main.
# ---------------------------------------------------------------------------


def _run_cli_subprocess(store: _ParityStore, argv: list[str]) -> dict[str, Any]:
    """Run ``python -m kairix.cli <argv> --json`` as a hermetic subprocess.

    Returns the parsed ``--json`` envelope. The subcommands seeded here always
    succeed (returncode 0), so a non-zero exit is a hard failure surfaced with
    the captured stderr.
    """
    result = subprocess.run(
        [sys.executable, "-m", "kairix.cli", *argv, _FLAG_JSON],
        capture_output=True,
        text=True,
        env=store.subprocess_env(),
        timeout=_SUBPROCESS_TIMEOUT_S,
    )
    assert result.returncode == 0, (
        f"CLI {argv!r} exited {result.returncode}\nstderr: {result.stderr}\nstdout: {result.stdout}"
    )
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def _run_cli_search_in_process(store: _ParityStore) -> dict[str, Any]:
    """Run the REAL ``kairix search`` CLI entry point in-process over the fake pipeline.

    ``kairix.core.search.cli.main`` is the shipped CLI code path (argparse →
    ``run_search`` → ``--json`` envelope); we drive it in-process with the
    injected composed ``search_deps`` because a ``kairix search`` subprocess
    cannot run offline (see module docstring). Returns the parsed envelope.
    """
    from kairix.core.search import cli as search_cli

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.suppress(SystemExit):
        search_cli.main([_TERM, _FLAG_JSON], deps=store.search_deps)
    parsed: dict[str, Any] = json.loads(buffer.getvalue())
    return parsed


# ---------------------------------------------------------------------------
# Parity driver — one per deep-tested capability.
# ---------------------------------------------------------------------------


def _no_breadcrumbs(_env: dict[str, Any]) -> list[str]:
    """Breadcrumb extractor for non-breadcrumb surfaces (bootstrap / usage_guide)."""
    return []


@dataclass(frozen=True)
class _ParityCase:
    """One capability's cross-entry-point parity driver.

    ``run_cli`` / ``run_mcp`` each execute the capability through one real entry
    point against the shared ``parity_store`` and return a JSON envelope.
    ``parity_view`` projects an envelope to the comparable data (the assertion
    compares data, not rendered bytes). ``breadcrumbs`` extracts the ordered
    resolvable ``source_uri`` list (empty for non-breadcrumb surfaces).
    """

    capability: str
    carries_breadcrumb: bool
    run_cli: Callable[[_ParityStore], dict[str, Any]]
    run_mcp: Callable[[_ParityStore], dict[str, Any]]
    parity_view: Callable[[dict[str, Any]], Any]
    breadcrumbs: Callable[[dict[str, Any]], list[str]] = _no_breadcrumbs
    cli_ceiling_ms: float = _CLI_SUBPROCESS_CEILING_MS
    mcp_ceiling_ms: float = _INPROCESS_CEILING_MS


def _uris(rows: list[dict[str, Any]]) -> list[str]:
    """Ordered ``source_uri`` values from a list of result-row dicts."""
    return [str(row.get(_K_SOURCE_URI, "")) for row in rows]


# --- search ---------------------------------------------------------------


def _search_view(env: dict[str, Any]) -> Any:
    return [(r.get("path"), r.get(_K_SOURCE_URI), r.get("seq")) for r in env.get("results", [])]


def _search_uris(env: dict[str, Any]) -> list[str]:
    return _uris(env.get("results", []))


def _search_mcp(store: _ParityStore) -> dict[str, Any]:
    envelope = tool_search(query=_TERM, deps=store.search_deps)
    # tool_search returns dict | str (the str branch is the queue-accepted path,
    # unreachable here with the queue flag OFF); narrow to the envelope dict.
    assert isinstance(envelope, dict), f"expected a search envelope dict, got {type(envelope)}"
    return envelope


# --- expand ---------------------------------------------------------------


def _expand_view(env: dict[str, Any]) -> Any:
    return [(c.get("seq"), c.get("path"), c.get(_K_SOURCE_URI), c.get("is_match")) for c in env.get("chunks", [])]


def _expand_uris(env: dict[str, Any]) -> list[str]:
    return _uris(env.get("chunks", []))


def _expand_cli(store: _ParityStore) -> dict[str, Any]:
    return _run_cli_subprocess(store, ["expand", _SOURCE_URI, _FLAG_DB_PATH, str(store.db_path)])


def _expand_mcp(store: _ParityStore) -> dict[str, Any]:
    repo = SQLiteDocumentRepository(store.db_path)
    deps = ExpandDeps(get_chunk=repo.get_by_path, list_chunk_seqs=repo.list_chunk_seqs)
    return tool_expand(source_uri=_SOURCE_URI, deps=deps)


# --- facts_about ----------------------------------------------------------


def _facts_view(env: dict[str, Any]) -> Any:
    return [(h.get("entity"), h.get("attribute"), h.get("value"), h.get(_K_SOURCE_URI)) for h in env.get("hits", [])]


def _facts_uris(env: dict[str, Any]) -> list[str]:
    return _uris(env.get("hits", []))


def _facts_cli(store: _ParityStore) -> dict[str, Any]:
    return _run_cli_subprocess(store, ["facts-about", _AGENT, _FLAG_DB_PATH, str(store.db_path)])


def _facts_mcp(store: _ParityStore) -> dict[str, Any]:
    # canonicals=[] pins the operator-canonical leg to empty on both surfaces
    # (the subprocess resolves [] from the tmp-XDG no-config env); the fact
    # breadcrumb is the property under test.
    return tool_facts_about(entity=_AGENT, paths=store.fake_paths, canonicals=[])


# --- bootstrap (no breadcrumb — orient surface) ---------------------------


def _bootstrap_view(env: dict[str, Any]) -> Any:
    return {
        "agent": env.get("agent"),
        "role": env.get("role"),
        "board": env.get("board"),
        "active_goals": env.get("active_goals"),
    }


def _bootstrap_cli(store: _ParityStore) -> dict[str, Any]:
    return _run_cli_subprocess(store, ["bootstrap", _AGENT, "--document-root", str(store.document_root)])


def _bootstrap_mcp(store: _ParityStore) -> dict[str, Any]:
    return tool_bootstrap(agent=_AGENT, deps=BootstrapDeps(document_root_fn=lambda: store.document_root))


# --- usage_guide (no breadcrumb — bundled agent guide) --------------------


def _usage_view(env: dict[str, Any]) -> Any:
    return {"topic": env.get("topic"), "content": env.get("content")}


def _usage_cli(store: _ParityStore) -> dict[str, Any]:
    return _run_cli_subprocess(store, ["usage-guide"])


def _usage_mcp(store: _ParityStore) -> dict[str, Any]:
    return tool_usage_guide(topic="")


_PARITY_CASES: tuple[_ParityCase, ...] = (
    _ParityCase(
        capability="search",
        carries_breadcrumb=True,
        run_cli=_run_cli_search_in_process,
        run_mcp=_search_mcp,
        parity_view=_search_view,
        breadcrumbs=_search_uris,
        cli_ceiling_ms=_INPROCESS_CEILING_MS,  # in-process real main (see module docstring)
    ),
    _ParityCase(
        capability="expand",
        carries_breadcrumb=True,
        run_cli=_expand_cli,
        run_mcp=_expand_mcp,
        parity_view=_expand_view,
        breadcrumbs=_expand_uris,
    ),
    _ParityCase(
        capability="facts_about",
        carries_breadcrumb=True,
        run_cli=_facts_cli,
        run_mcp=_facts_mcp,
        parity_view=_facts_view,
        breadcrumbs=_facts_uris,
    ),
    _ParityCase(
        capability="bootstrap",
        carries_breadcrumb=False,
        run_cli=_bootstrap_cli,
        run_mcp=_bootstrap_mcp,
        parity_view=_bootstrap_view,
    ),
    _ParityCase(
        capability="usage_guide",
        carries_breadcrumb=False,
        run_cli=_usage_cli,
        run_mcp=_usage_mcp,
        parity_view=_usage_view,
    ),
)


# ---------------------------------------------------------------------------
# Tier 2 — cross-entry-point behaviour + breadcrumb + latency parity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", _PARITY_CASES, ids=lambda c: c.capability)
def test_cli_mcp_cross_entry_point_parity(case: _ParityCase, parity_store: _ParityStore) -> None:
    """CLI and MCP agree on behaviour + breadcrumb, and each stays under budget.

    Sabotage-proof (executed): overwrote each chunk's ``source_uri`` with a
    sentinel inside the MCP ``tool_expand`` adapter (after
    ``expand_output_to_envelope``) so the MCP surface diverged from the CLI
    subprocess → the ``expand`` case's data-equivalence assertion (b) AND the
    breadcrumb-equality assertion (c) both failed while the CLI kept the real
    ``source_uri``. Reverted the adapter and the case went green again.
    """
    t0 = time.monotonic()
    cli_env = case.run_cli(parity_store)
    cli_ms = (time.monotonic() - t0) * 1000.0

    t1 = time.monotonic()
    mcp_env = case.run_mcp(parity_store)
    mcp_ms = (time.monotonic() - t1) * 1000.0

    # (a) both surfaces succeeded.
    assert cli_env.get(_K_ERROR, "") == "", f"{case.capability}: CLI surface errored: {cli_env.get(_K_ERROR)!r}"
    assert mcp_env.get(_K_ERROR, "") == "", f"{case.capability}: MCP surface errored: {mcp_env.get(_K_ERROR)!r}"

    # (b) underlying data equivalence (not byte-equality — one renders, one dicts).
    cli_view = case.parity_view(cli_env)
    mcp_view = case.parity_view(mcp_env)
    assert cli_view == mcp_view, (
        f"{case.capability}: CLI vs MCP data diverged.\n  CLI: {cli_view!r}\n  MCP: {mcp_view!r}"
    )

    # (c) the resolvable source_uri breadcrumb is present + EQUAL across surfaces.
    if case.carries_breadcrumb:
        cli_uris = case.breadcrumbs(cli_env)
        mcp_uris = case.breadcrumbs(mcp_env)
        assert cli_uris, f"{case.capability}: CLI surface returned no source_uri breadcrumb"
        assert mcp_uris, f"{case.capability}: MCP surface returned no source_uri breadcrumb"
        assert all(cli_uris), f"{case.capability}: CLI breadcrumb had an empty (unresolvable) source_uri: {cli_uris}"
        assert cli_uris == mcp_uris, (
            f"{case.capability}: source_uri breadcrumb diverged.\n  CLI: {cli_uris}\n  MCP: {mcp_uris}"
        )

    # (d) per-surface latency ceiling (the perf-regression guard).
    assert cli_ms < case.cli_ceiling_ms, (  # F82-allowed: e2e cross-entry-point latency guard
        f"{case.capability}: CLI surface took {cli_ms:.0f}ms (ceiling {case.cli_ceiling_ms:.0f}ms)"
    )
    assert mcp_ms < case.mcp_ceiling_ms, (  # F82-allowed: e2e cross-entry-point latency guard
        f"{case.capability}: MCP surface took {mcp_ms:.0f}ms (ceiling {case.mcp_ceiling_ms:.0f}ms)"
    )


# ---------------------------------------------------------------------------
# Tier 1 — catalogue drift-lock over the WHOLE agent-facing surface.
# ---------------------------------------------------------------------------


def _shipped_cli_subcommand(cap: Capability) -> str | None:
    """Return the shipped ``kairix <sub>`` subcommand backing ``cap``, or None.

    Mirrors the catalogue→CLI derivation: the first token after ``kairix`` when
    it is itself a command (``entity`` / ``doctor`` / …), or the hyphen-joined
    two-word form the shipped table uses (``facts about`` → ``facts-about``).
    Returns ``None`` for capabilities whose ``cli`` is not a ``kairix …``
    invocation (the Python-API-only ``probe_search``).
    """
    parts = cap.cli.split()
    if len(parts) < 2 or parts[0] != "kairix":
        return None
    if parts[1] in COMMANDS:
        return parts[1]
    if len(parts) >= 3:
        joined = f"{parts[1]}-{parts[2]}"
        if joined in COMMANDS:
            return joined
    return None


@pytest.mark.parametrize("cap", agent_facing(), ids=lambda c: c.name)
def test_agent_facing_capability_is_dual_wired(cap: Capability) -> None:
    """Every agent-facing capability is reachable via BOTH the CLI and MCP.

    Derived from the catalogue, so a new agent-facing capability is
    auto-enumerated (and fails here until wired) and a removed one drops out —
    the "consistency can't silently drift" backbone over the whole surface.
    The MCP-registration half is proved once by
    :func:`test_build_server_registers_every_capability`.

    Sabotage-proof (executed): drop a capability's ``_CLI_HANDLERS`` wiring in
    ``kairix/cli.py`` so its subcommand leaves ``COMMANDS`` → this assertion
    fails for that capability. Restored.
    """
    assert cap.mcp_tool is not None, f"{cap.name}: agent_facing() must expose an MCP tool"
    if cap.name in _MCP_ONLY_CAPABILITIES:
        assert _shipped_cli_subcommand(cap) is None, (
            f"{cap.name}: documented MCP-only, but a CLI subcommand now resolves — "
            f"drop it from _MCP_ONLY_CAPABILITIES if it is now dual-wired."
        )
        return
    sub = _shipped_cli_subcommand(cap)
    assert sub is not None and sub in COMMANDS, (
        f"{cap.name}: agent-facing but no CLI subcommand resolves from cli={cap.cli!r}. "
        f"fix: wire it in kairix/cli.py _CLI_HANDLERS, or add it to _MCP_ONLY_CAPABILITIES "
        f"if it is deliberately MCP-only."
    )


def test_build_server_registers_every_capability() -> None:
    """``build_server()`` registers a tool for every catalogue row without raising.

    ``_register_from_catalogue`` KeyErrors if any ``mcp_tool`` / ``escalate_via``
    lacks a binding, so a clean build is the MCP-side registration proof for the
    whole catalogue — the other half of the dual-wired drift-lock.

    Sabotage-proof (executed): remove a domain binding from its adapter
    ``BINDINGS`` tuple → ``build_server()`` raises KeyError here. Restored.
    """
    server = build_server()
    assert server is not None


def test_parity_cases_are_agent_facing_and_unique() -> None:
    """The deep-parity driver set is a subset of ``agent_facing()`` — no stale driver.

    Removing a capability from the catalogue can't leave a stale parity driver
    behind (it would fail this membership check), so the two tiers stay locked
    to the same source of truth.
    """
    facing = {cap.name for cap in agent_facing()}
    covered = [case.capability for case in _PARITY_CASES]
    assert len(covered) == len(set(covered)), f"duplicate parity driver(s): {covered}"
    stale = set(covered) - facing
    assert not stale, f"parity driver(s) name non-agent-facing capabilities: {sorted(stale)}"
