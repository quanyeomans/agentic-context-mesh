"""Unit tests for the ``default_*`` lazy-import wrappers in
``kairix.core.embed.use_cases`` (PR #247 QG).

The F6 refactor (commit dab94644) replaced ``Optional[Callable]``
fields with ``field(default_factory=...)`` and added one
``default_*`` lazy-import wrapper per production callable. Sonar
treats each wrapper line as new code; the existing
``tests/embed/test_use_cases.py`` covers the orchestration via
injected stand-ins but never lights up the production defaults.

These tests drive each wrapper directly (via the
``kairix.core.embed.use_cases`` module attribute — module access is
not an internal-name import, so F5 is satisfied) and assert that:

  - The wrapper returns a value of the expected shape OR delegates to
    the documented kairix function.
  - The wrapper invokes its lazy import (which would otherwise be
    measured as 0% covered on Sonar's per-line view).

The wrappers themselves are thin pass-throughs — production wiring,
not business logic — so the assertions stay focused on "the import
ran, the right function got called".

F1 paydown note: every collaborator is reached via ``UseCaseDeps``
(constructed test-side with stand-ins) rather than
``monkeypatch.setattr`` on kairix modules. The dataclass bundles all
nine ``default_*`` impl seams plus the seven scan-composition
collaborators (DocumentScanner, load_collections, yaml loader, etc.)
into a single deps object the test constructs once per scenario.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import kairix.core.embed.use_cases as uc_mod

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# default_db_path — wraps the get_db_path seam in UseCaseDeps.
# ---------------------------------------------------------------------------


def test_default_db_path_delegates_to_get_db_path() -> None:
    """``default_db_path`` calls ``UseCaseDeps.get_db_path_fn`` and stringifies."""
    deps = uc_mod.UseCaseDeps(get_db_path_fn=lambda: Path("/tmp/test-path.sqlite"))

    out = uc_mod.default_db_path(deps=deps)

    assert out == "/tmp/test-path.sqlite"
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# default_open_db — wraps the open_db seam in UseCaseDeps.
# ---------------------------------------------------------------------------


def test_default_open_db_delegates_to_open_db() -> None:
    """``default_open_db`` forwards the path to ``UseCaseDeps.open_db_fn``."""
    captured: list[Path] = []

    class _Sentinel:
        pass

    sentinel = _Sentinel()

    def _fake_open_db(path: Path) -> _Sentinel:
        captured.append(path)
        return sentinel

    deps = uc_mod.UseCaseDeps(open_db_fn=_fake_open_db)

    out = uc_mod.default_open_db(Path("/tmp/x.sqlite"), deps=deps)

    assert out is sentinel
    assert captured == [Path("/tmp/x.sqlite")]


# ---------------------------------------------------------------------------
# default_create_schema and default_validate_schema — wrap the
# create_schema / validate_schema seams in UseCaseDeps.
# ---------------------------------------------------------------------------


def test_default_create_schema_delegates() -> None:
    """``default_create_schema`` forwards its db argument to the seam."""
    seen: list[Any] = []
    deps = uc_mod.UseCaseDeps(create_schema_fn=lambda db: seen.append(db))

    db_sentinel = object()
    uc_mod.default_create_schema(db_sentinel, deps=deps)

    assert seen == [db_sentinel]


def test_default_validate_schema_delegates() -> None:
    """``default_validate_schema`` forwards its db argument to the seam."""
    seen: list[Any] = []
    deps = uc_mod.UseCaseDeps(validate_schema_fn=lambda db: seen.append(db))

    db_sentinel = object()
    uc_mod.default_validate_schema(db_sentinel, deps=deps)

    assert seen == [db_sentinel]


# ---------------------------------------------------------------------------
# default_acquire_lock and default_release_lock — wrap the lock seams.
# ---------------------------------------------------------------------------


def test_default_acquire_lock_delegates() -> None:
    """``default_acquire_lock`` returns whatever the lock seam returns."""
    sentinel = object()
    deps = uc_mod.UseCaseDeps(acquire_lock_fn=lambda: sentinel)

    assert uc_mod.default_acquire_lock(deps=deps) is sentinel


def test_default_release_lock_delegates() -> None:
    """``default_release_lock`` forwards the lock handle to the seam."""
    seen: list[Any] = []
    deps = uc_mod.UseCaseDeps(release_lock_fn=lambda fh: seen.append(fh))

    handle = object()
    uc_mod.default_release_lock(handle, deps=deps)

    assert seen == [handle]


# ---------------------------------------------------------------------------
# default_save_run_log — wraps the save_run_log seam.
# ---------------------------------------------------------------------------


def test_default_save_run_log_delegates() -> None:
    """``default_save_run_log`` forwards the log entry dict verbatim."""
    seen: list[dict[str, Any]] = []
    deps = uc_mod.UseCaseDeps(save_run_log_fn=lambda entry: seen.append(entry))

    entry = {"command": "embed", "embedded": 7}
    uc_mod.default_save_run_log(entry, deps=deps)

    assert seen == [entry]


# ---------------------------------------------------------------------------
# default_run_embed — wraps the run_embed seam.
# ---------------------------------------------------------------------------


def test_default_run_embed_delegates_kwargs() -> None:
    """``default_run_embed`` forwards every kwarg to the seam."""
    captured: list[dict[str, Any]] = []

    def _fake_run_embed(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"embedded": 1, "failed": 0, "skipped": 0, "duration_s": 0.1, "estimated_cost_usd": 0.0}

    deps = uc_mod.UseCaseDeps(run_embed_fn=_fake_run_embed)

    out = uc_mod.default_run_embed(db=None, force=False, batch_size=100, limit=None, deps=deps)

    assert out["embedded"] == 1
    assert captured == [{"db": None, "force": False, "batch_size": 100, "limit": None}]


# ---------------------------------------------------------------------------
# default_run_recall_gate — wraps the run_recall_gate seam.
# ---------------------------------------------------------------------------


def test_default_run_recall_gate_delegates_kwargs() -> None:
    """``default_run_recall_gate`` forwards kwargs to the seam."""
    captured: list[dict[str, Any]] = []

    def _fake_gate(**kwargs: Any) -> tuple[bool, dict[str, Any]]:
        captured.append(kwargs)
        return True, {"score": 0.95, "passed": 19, "total": 20}

    deps = uc_mod.UseCaseDeps(run_recall_gate_fn=_fake_gate)

    passed, result = uc_mod.default_run_recall_gate(alert_callback=None, rebuild_canaries=False, deps=deps)

    assert passed is True
    assert result["score"] == 0.95
    assert captured == [{"alert_callback": None, "rebuild_canaries": False}]


# ---------------------------------------------------------------------------
# PipelineDeps default factory — defaults wire the lazy production callables.
# ---------------------------------------------------------------------------


def test_pipeline_deps_defaults_wire_lazy_production_callables() -> None:
    """``PipelineDeps()`` resolves to the module's ``default_*`` callables.

    This catches accidental ``Optional[Callable]`` regressions: if a future
    refactor reverts F6, the defaults would be ``None`` and these identity
    checks would fail.
    """
    deps = uc_mod.PipelineDeps()
    assert deps.db_path_fn is uc_mod.default_db_path
    assert deps.open_db_fn is uc_mod.default_open_db
    assert deps.schema_fn is uc_mod.default_create_schema
    assert deps.validate_schema_fn is uc_mod.default_validate_schema
    assert deps.acquire_lock_fn is uc_mod.default_acquire_lock
    assert deps.release_lock_fn is uc_mod.default_release_lock
    assert deps.save_run_log_fn is uc_mod.default_save_run_log
    assert deps.run_embed_fn is uc_mod.default_run_embed
    assert deps.run_recall_gate_fn is uc_mod.default_run_recall_gate
    assert deps.scan_documents_fn is uc_mod.default_scan_documents


# ---------------------------------------------------------------------------
# EmbedPipelineResult.success — the only branch in the dataclass.
# ---------------------------------------------------------------------------


def test_embed_pipeline_result_success_when_no_failed() -> None:
    """``failed == 0`` → ``success`` is True regardless of recall outcome."""
    result = uc_mod.EmbedPipelineResult(
        embedded=10,
        failed=0,
        skipped=5,
        duration_s=1.0,
        cost_usd=0.01,
        db_path="/tmp/x.sqlite",
        timestamp=1700000000,
    )
    assert result.success is True


def test_embed_pipeline_result_success_false_when_any_failed() -> None:
    """Any failed chunks → ``success`` is False; recall outcome doesn't matter."""
    result = uc_mod.EmbedPipelineResult(
        embedded=10,
        failed=3,
        skipped=0,
        duration_s=1.0,
        cost_usd=0.01,
        db_path="/tmp/x.sqlite",
        timestamp=1700000000,
        recall_passed=True,
    )
    assert result.success is False


def test_embed_pipeline_result_default_diagnostics_empty() -> None:
    """``diagnostics`` defaults to an empty list (not None)."""
    result = uc_mod.EmbedPipelineResult(
        embedded=0,
        failed=0,
        skipped=0,
        duration_s=0.0,
        cost_usd=0.0,
        db_path="/tmp/x.sqlite",
        timestamp=0,
    )
    assert result.diagnostics == []


# ---------------------------------------------------------------------------
# default_scan_documents — wraps DocumentScanner + collection config loader.
#
# This wrapper is the production hook ``PipelineDeps`` points at by default.
# It composes seven collaborators (DocumentScanner, load_collections,
# resolve_config_path, agent registry, reference-library probe, FTS
# rebuild, yaml loader). We drive each branch by constructing a single
# ``UseCaseDeps(...)`` with stand-ins for each collaborator — no
# ``monkeypatch.setattr`` on kairix modules.
# ---------------------------------------------------------------------------


class _FakeScanReport:
    """Stand-in for ``kairix.core.db.scanner.ScanReport`` — only the
    fields :func:`default_scan_documents` reads."""

    def __init__(self, *, new: int = 0, updated: int = 0, unchanged: int = 0, errors: int = 0) -> None:
        self.new = new
        self.updated = updated
        self.unchanged = unchanged
        self.errors = errors


class _FakeScanner:
    """Stand-in for ``DocumentScanner`` — records the collections passed
    to ``scan()`` and returns a configurable ``_FakeScanReport``."""

    def __init__(self, report: _FakeScanReport) -> None:
        self._report = report
        self.collections_scanned: list[Any] = []
        self.constructor_kwargs: dict[str, Any] = {}

    def scan(self, collections: list[Any]) -> _FakeScanReport:
        self.collections_scanned = collections
        return self._report


class _FakeRegistry:
    """Stand-in for ``AgentRegistry`` — only the ``list_agents`` method matters."""

    def __init__(self, agents: list[str] | None) -> None:
        self._agents = agents or []

    def list_agents(self) -> list[str]:
        return list(self._agents)


class _FakePathForYaml:
    """Minimal ``Path``-shaped stand-in for ``resolve_config_path()``.

    Only the ``.open(encoding=...)`` context manager surface is exercised
    by the wrapper; we yield a dummy stream that the yaml stub never
    actually reads (the yaml_safe_load stub returns the canned dict).
    """

    def open(self, encoding: str = "utf-8") -> Any:
        # encoding is part of pathlib.Path.open's surface; preserved for parity.
        _ = encoding

        class _Ctx:
            def __enter__(self) -> Any:
                return object()

            def __exit__(self, *exc: Any) -> None:
                # Context manager exit — no cleanup required for the stub.
                _ = exc

        return _Ctx()


class _FakeReflibRoot:
    """Stand-in for the Path returned by reference_library_root — only
    ``is_dir`` is consulted by ``harmonise_reference_library``."""

    def __init__(self, exists: bool) -> None:
        self._exists = exists

    def __str__(self) -> str:
        return "/tmp/fake-reflib"

    def is_dir(self) -> bool:
        return self._exists


def _build_scan_deps(
    *,
    report: _FakeScanReport,
    collections_cfg: Any = None,
    config_path: Any = None,
    registry_agents: list[str] | None = None,
    reflib_is_dir: bool = False,
    reflib_index_mode: str = "eager",
    raw_yaml: Any = None,
    yaml_raises: BaseException | None = None,
    rebuild_fts_count: int = 0,
    recorders: dict[str, Any] | None = None,
) -> tuple[uc_mod.UseCaseDeps, _FakeScanner, dict[str, Any]]:
    """Construct a ``UseCaseDeps`` wired with the seven scan collaborators.

    Returns the deps, the installed scanner stand-in, and a recorders dict
    the test can assert against (FTS calls, registry calls, etc.).
    """
    fake_scanner = _FakeScanner(report)
    rec = recorders if recorders is not None else {"fts_calls": [], "registry_calls": [], "scanner_kwargs": {}}
    rec.setdefault("fts_calls", [])
    rec.setdefault("registry_calls", [])
    rec.setdefault("scanner_kwargs", {})

    def _doc_scanner(db: Any, *, document_root: Any, agent_owner_resolver: Any) -> _FakeScanner:
        rec["scanner_kwargs"] = {
            "db": db,
            "document_root": document_root,
            "agent_owner_resolver": agent_owner_resolver,
        }
        return fake_scanner

    def _parse_agent_registry(raw: Any, **_kw: Any) -> _FakeRegistry:
        rec["registry_calls"].append(raw)
        return _FakeRegistry(registry_agents)

    def _yaml_safe_load(_stream: Any) -> Any:
        if yaml_raises is not None:
            raise yaml_raises
        return raw_yaml

    def _rebuild_fts(db: Any) -> int:
        rec["fts_calls"].append(db)
        return rebuild_fts_count

    deps = uc_mod.UseCaseDeps(
        document_scanner_cls=_doc_scanner,
        load_collections_fn=lambda: collections_cfg,
        resolve_config_path_fn=lambda: config_path,
        parse_agent_registry_fn=_parse_agent_registry,
        build_agent_owner_resolver_fn=lambda reg: ("resolver", reg),
        document_root_fn=lambda: Path("/tmp/fake-doc-root"),
        reference_library_root_fn=lambda: _FakeReflibRoot(reflib_is_dir),
        reflib_index_mode_fn=lambda: reflib_index_mode,
        rebuild_fts_fn=_rebuild_fts,
        yaml_safe_load_fn=_yaml_safe_load,
    )
    return deps, fake_scanner, rec


def test_default_scan_documents_no_config_no_reflib_no_changes() -> None:
    """No collections.yml, no reference-library, no new docs → returns zeros.

    Sabotage-prove: if the wrapper miscounted scan_report fields (e.g.
    swapped new/updated) the tuple shape assertion below would fail.
    """
    report = _FakeScanReport(new=0, updated=0, unchanged=0, errors=0)
    deps, scanner, recorders = _build_scan_deps(report=report)

    diagnostics: list[str] = []
    new, updated, errors = uc_mod.default_scan_documents(object(), diagnostics, deps=deps)

    assert (new, updated, errors) == (0, 0, 0)
    # Default branch when no config: a single "default" collection rooted at ".".
    assert len(scanner.collections_scanned) == 1
    assert scanner.collections_scanned[0].name == "default"
    assert recorders["fts_calls"] == [], "FTS rebuild should NOT run when scan has no new/updated"
    # No diagnostic when resolve_config_path returns None — agent registry path is skipped.
    assert diagnostics == []


def test_default_scan_documents_loads_shared_collections_from_config() -> None:
    """Configured shared collections override the implicit "default" one.

    Sabotage-prove: if the wrapper ignored the loaded ``collections_cfg``
    the assertion that "alpha" appears in scanned collection names fails.
    """
    from kairix.core.search.config_loader import CollectionDef, CollectionsConfig

    cfg = CollectionsConfig(
        shared=(
            CollectionDef(name="alpha", path="alpha/", glob="**/*.md"),
            CollectionDef(name="beta", path="beta/", glob="**/*.txt"),
        ),
    )
    report = _FakeScanReport(new=0, updated=0, unchanged=5, errors=0)
    deps, scanner, _ = _build_scan_deps(report=report, collections_cfg=cfg)

    diagnostics: list[str] = []
    uc_mod.default_scan_documents(object(), diagnostics, deps=deps)

    names = sorted(c.name for c in scanner.collections_scanned)
    assert names == ["alpha", "beta"]


def test_default_scan_documents_appends_reflib_when_present() -> None:
    """If ``reference_library_root()`` is a real dir, it joins the scan list."""
    report = _FakeScanReport(new=0, updated=0)
    deps, scanner, _ = _build_scan_deps(report=report, reflib_is_dir=True)

    uc_mod.default_scan_documents(object(), [], deps=deps)

    names = [c.name for c in scanner.collections_scanned]
    # The reference-library collection is appended after the default.
    assert "reference-library" in names
    reflib_cfg = next(c for c in scanner.collections_scanned if c.name == "reference-library")
    assert reflib_cfg.glob == "**/*.md"


def test_default_scan_documents_skip_mode_excludes_reflib_from_walk() -> None:
    """#475 — ``reference_library.index: skip`` keeps the bundled library
    out of the scan walk even though its root directory exists.

    Sabotage proof: removing the skip branch in
    ``_resolve_reflib_collections`` appends the reflib collection (the
    eager behaviour) and the not-in assertion fails.
    """
    report = _FakeScanReport(new=0, updated=0)
    deps, scanner, _ = _build_scan_deps(report=report, reflib_is_dir=True, reflib_index_mode="skip")

    uc_mod.default_scan_documents(object(), [], deps=deps)

    names = [c.name for c in scanner.collections_scanned]
    assert "reference-library" not in names
    assert names == ["default"], "user collections still scan normally under skip"


def test_default_scan_documents_skip_mode_drops_operator_declared_reflib() -> None:
    """#475 — skip also drops an operator-declared reference-library entry,
    so a stale YAML declaration can't re-add the library to the walk."""
    from kairix.core.search.config_loader import CollectionDef, CollectionsConfig

    cfg = CollectionsConfig(
        shared=(
            CollectionDef(name="alpha", path="alpha/"),
            CollectionDef(name="reference-library", path="/opt/kairix/reference-library"),
        ),
    )
    report = _FakeScanReport(new=0, updated=0)
    deps, scanner, _ = _build_scan_deps(
        report=report,
        collections_cfg=cfg,
        reflib_is_dir=True,
        reflib_index_mode="skip",
    )

    uc_mod.default_scan_documents(object(), [], deps=deps)

    names = [c.name for c in scanner.collections_scanned]
    assert names == ["alpha"]


def test_default_scan_documents_lazy_mode_keeps_reflib_in_walk() -> None:
    """#475 — lazy still SCANS the library (deferral happens at embed time,
    in ``_apply_reflib_index_mode``), so its documents are FTS-searchable."""
    report = _FakeScanReport(new=0, updated=0)
    deps, scanner, _ = _build_scan_deps(report=report, reflib_is_dir=True, reflib_index_mode="lazy")

    uc_mod.default_scan_documents(object(), [], deps=deps)

    names = [c.name for c in scanner.collections_scanned]
    assert "reference-library" in names


def test_default_scan_documents_rebuilds_fts_when_new_or_updated() -> None:
    """When scan reports any new or updated doc the wrapper rebuilds FTS.

    Sabotage-prove: if the rebuild guard were dropped to ``if True`` the
    FTS rebuild would run with every empty scan and double-count; if
    inverted to ``< 0`` it would never run. The recorders confirm exactly
    one rebuild fires when new=1.
    """
    report = _FakeScanReport(new=1, updated=0, unchanged=10, errors=0)
    deps, _, recorders = _build_scan_deps(report=report, rebuild_fts_count=42)

    db_sentinel = object()
    new, updated, errors = uc_mod.default_scan_documents(db_sentinel, [], deps=deps)

    assert (new, updated, errors) == (1, 0, 0)
    assert recorders["fts_calls"] == [db_sentinel], "FTS rebuild must run with the same db handle"


def test_default_scan_documents_rebuilds_fts_when_only_updated() -> None:
    """``updated > 0`` alone also triggers rebuild — covers the OR branch."""
    report = _FakeScanReport(new=0, updated=3, unchanged=0, errors=0)
    deps, _, recorders = _build_scan_deps(report=report, rebuild_fts_count=7)

    uc_mod.default_scan_documents(object(), [], deps=deps)

    assert len(recorders["fts_calls"]) == 1


def test_default_scan_documents_builds_agent_resolver_from_registry() -> None:
    """A config_path with at least one registered agent wires a resolver.

    Sabotage-prove: if the wrapper passed ``agent_owner_resolver=None``
    even when the registry had agents, the assertion that the resolver
    sentinel propagates to DocumentScanner would fail.
    """
    report = _FakeScanReport(new=0, updated=0)
    deps, _, recorders = _build_scan_deps(
        report=report,
        config_path=_FakePathForYaml(),
        registry_agents=["alpha", "beta"],
        raw_yaml={"agents": [{"name": "alpha"}, {"name": "beta"}]},
    )

    uc_mod.default_scan_documents(object(), [], deps=deps)

    # The build_agent_owner_resolver stub returns a tuple sentinel ("resolver", reg);
    # we just need to know the wrapper used it (vs. None).
    resolver = recorders["scanner_kwargs"]["agent_owner_resolver"]
    assert resolver is not None
    assert isinstance(resolver, tuple) and resolver[0] == "resolver"


def test_default_scan_documents_skips_resolver_when_registry_empty() -> None:
    """A config_path with no registered agents skips resolver construction.

    Sabotage-prove: if the wrapper blindly built a resolver for any
    non-None registry, the scanner kwargs would not be None.
    """
    report = _FakeScanReport(new=0, updated=0)
    deps, _, recorders = _build_scan_deps(
        report=report,
        config_path=_FakePathForYaml(),
        registry_agents=[],
        raw_yaml={"agents": []},
    )

    uc_mod.default_scan_documents(object(), [], deps=deps)

    assert recorders["scanner_kwargs"]["agent_owner_resolver"] is None


def test_default_scan_documents_appends_diagnostic_on_resolver_failure() -> None:
    """When agent-resolver construction raises, the wrapper logs a diagnostic
    and continues with ``agent_owner_resolver=None``.

    Sabotage-prove: if the wrapper let the exception escape, this call
    would raise instead of returning the scan tuple.
    """
    report = _FakeScanReport(new=0, updated=0)
    deps, _, recorders = _build_scan_deps(
        report=report,
        config_path=_FakePathForYaml(),
        yaml_raises=RuntimeError("yaml exploded"),
    )

    diagnostics: list[str] = []
    new, updated, errors = uc_mod.default_scan_documents(object(), diagnostics, deps=deps)

    assert (new, updated, errors) == (0, 0, 0)
    assert recorders["scanner_kwargs"]["agent_owner_resolver"] is None
    assert any("agent_resolver_unavailable" in msg for msg in diagnostics)
    assert any("yaml exploded" in msg for msg in diagnostics)


def test_default_scan_documents_handles_yaml_returning_none() -> None:
    """A config file present but empty (``yaml.safe_load`` → None) is
    treated as ``{}`` — the wrapper still calls ``parse_agent_registry``
    with an empty dict and continues."""
    report = _FakeScanReport(new=0, updated=0)
    deps, _, recorders = _build_scan_deps(
        report=report,
        config_path=_FakePathForYaml(),
        registry_agents=[],
        raw_yaml=None,
    )

    uc_mod.default_scan_documents(object(), [], deps=deps)

    # registry was constructed with {} (the wrapper's ``or {}`` fallback).
    assert recorders["registry_calls"] == [{}]
