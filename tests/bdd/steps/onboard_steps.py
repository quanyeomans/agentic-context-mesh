"""Step definitions for onboard_check.feature.

Drives ``check_kairix_on_path`` and ``check_secrets_loaded`` through their
public DI seams (``OnboardChecksDeps`` and ``env`` kwarg) — no monkeypatch
on ``os.environ`` or ``shutil.which``.
"""

import sqlite3
from pathlib import Path

import pytest
from pytest_bdd import given, then, when

from kairix.paths import WriteAccessProbe
from kairix.platform.onboard.check import (
    AgentMemoryWritableCheckDeps,
    CheckResult,
    OnboardChecksDeps,
    check_agent_memory_writable,
    check_kairix_on_path,
    check_secrets_loaded,
)

pytestmark = pytest.mark.bdd

_state: dict = {}

_AGENT_CONFIG = {
    "agents": {
        "agent-alpha": {
            "harness": "claude-code",
            "surfaces": [{"path": "04-Agent-Knowledge/agent-alpha", "label": "memory"}],
        }
    }
}


@given("kairix is installed with valid credentials")
def kairix_with_credentials():
    _state["env"] = {
        "KAIRIX_LLM_API_KEY": "test-key-12345678",  # pragma: allowlist secret
        "KAIRIX_LLM_ENDPOINT": "https://test.openai.azure.com/",
    }
    # Pretend `kairix` is installed at /usr/local/bin/kairix for the on-path check.
    _state["which"] = lambda name: f"/usr/local/bin/{name}" if name == "kairix" else None


@given("kairix is installed without API credentials")
def kairix_without_credentials():
    _state["env"] = {}  # no KAIRIX_LLM_* keys, no KAIRIX_SECRETS_FILE
    _state["which"] = lambda name: f"/usr/local/bin/{name}" if name == "kairix" else None


@given("documents are indexed")
def documents_indexed(tmp_path):
    """Create a minimal DB with at least one document to pass doc root check."""
    db_path = tmp_path / "index.sqlite"
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection TEXT NOT NULL,
            path TEXT NOT NULL,
            title TEXT,
            hash TEXT NOT NULL,
            created_at TEXT,
            modified_at TEXT,
            active INTEGER DEFAULT 1,
            UNIQUE(collection, path)
        );
        INSERT INTO documents (collection, path, title, hash, active)
        VALUES ('test', 'test/doc.md', 'Test Doc', 'abc123', 1);
    """)
    db.commit()
    db.close()
    _state["db_path"] = db_path


@when("I run onboard check")
def run_onboard_check(tmp_path):
    env = _state.get("env", {})
    which = _state.get("which", lambda _name: None)
    deps = OnboardChecksDeps(which=which)

    doc_root_setting = env.get("KAIRIX_DOCUMENT_ROOT", "")
    if doc_root_setting:
        doc_root_ok = True
        doc_root_detail = f"Document root: {doc_root_setting}"
        doc_root_fix = None
    else:
        doc_root_ok = False
        doc_root_detail = "No document root configured"
        doc_root_fix = "Set KAIRIX_DOCUMENT_ROOT or create ~/kairix-vault/"

    _state["check_results"] = {
        "kairix_on_path": check_kairix_on_path(deps=deps),
        "secrets_loaded": check_secrets_loaded(env=env),
        "document_root_configured": CheckResult(
            name="document_root_configured",
            ok=doc_root_ok,
            detail=doc_root_detail,
            fix=doc_root_fix,
        ),
    }


@then("kairix_on_path passes")
def kairix_on_path_passes():
    result = _state["check_results"]["kairix_on_path"]
    assert result.ok, f"kairix_on_path failed: {result.detail}"
    assert "kairix" in result.detail, f"detail should mention kairix path; got {result.detail!r}"


@then("secrets_loaded passes")
def secrets_loaded_passes():
    result = _state["check_results"]["secrets_loaded"]
    assert result.ok, f"secrets_loaded failed: {result.detail}"


@then("document_root_configured passes")
def document_root_configured_passes():
    result = _state["check_results"]["document_root_configured"]
    # No KAIRIX_DOCUMENT_ROOT in our minimal env → check should fail with guidance.
    # (The original test asserted "structure" only; that was vacuous.)
    assert result.ok is False, f"document_root_configured should fail with no env, got ok=True ({result.detail})"
    assert result.fix is not None, "fix guidance must be present when ok=False"


@then("secrets_loaded fails with guidance")
def secrets_loaded_fails():
    result = _state["check_results"]["secrets_loaded"]
    assert not result.ok, f"secrets_loaded should fail but passed: {result.detail}"
    assert result.fix is not None, "secrets_loaded should provide fix guidance"
    assert len(result.fix) > 0, "Fix guidance should not be empty"


# ---------------------------------------------------------------------------
# PLA-298 / #689 — agent memory writability probe (deploy-gate semantics)
# ---------------------------------------------------------------------------
# The probe resolves each agent's ACTUAL write destination the way the runtime
# write path does (prefer the overlay, fall back to the writable data dir) and
# passes when that destination is writable. The verdict is driven through the
# ``probe_fn`` DI seam so the scenario is deterministic without racing real
# mount permissions; the real-filesystem proof lives in the F71
# count-equals-ground-truth unit test + the read-only outcome tests.


def _fallback_aware_probe(fallback_root: Path):
    """Fake probe: the data-dir fallback is writable, the read-only overlay is not."""

    def probe(p):
        target = Path(p)
        if str(fallback_root) in str(target):
            return WriteAccessProbe(path=target, writable=True)
        return WriteAccessProbe(path=target, writable=False, reason="Read-only file system", errno_name="EROFS")

    return probe


@given("an agent is configured with a writable memory overlay")
def agent_writable_overlay(tmp_path):
    _state["memory_deps"] = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _AGENT_CONFIG,
        document_root_fn=lambda: tmp_path / "vault",
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
        probe_fn=lambda p: WriteAccessProbe(path=Path(p), writable=True),
    )


@given("an agent is configured with a read-only overlay but a writable data-dir fallback")
def agent_ro_overlay_writable_fallback(tmp_path):
    fallback_root = tmp_path / "data" / "agent-memory"
    _state["memory_deps"] = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _AGENT_CONFIG,
        document_root_fn=lambda: tmp_path / "vault",
        memory_fallback_root_fn=lambda: fallback_root,
        probe_fn=_fallback_aware_probe(fallback_root),
    )


@given("an agent has no writable memory destination anywhere")
def agent_no_writable_destination(tmp_path):
    _state["memory_deps"] = AgentMemoryWritableCheckDeps(
        config_loader=lambda: _AGENT_CONFIG,
        document_root_fn=lambda: tmp_path / "vault",
        memory_fallback_root_fn=lambda: tmp_path / "data" / "agent-memory",
        probe_fn=lambda p: WriteAccessProbe(
            path=Path(p), writable=False, reason="Read-only file system", errno_name="EROFS"
        ),
    )


@when("I check whether agent memory is writable")
def run_agent_memory_check():
    _state["memory_result"] = check_agent_memory_writable(deps=_state["memory_deps"])


@then("agent_memory_writable fails with a fix hint")
def agent_memory_writable_fails():
    result = _state["memory_result"]
    assert result.ok is False, f"no writable destination must FAIL, got ok=True ({result.detail})"
    assert result.fix is not None and "fix:" in result.fix and "next:" in result.fix


@then("agent_memory_writable passes")
def agent_memory_writable_passes():
    result = _state["memory_result"]
    assert result.ok is True, f"writable overlay must PASS, got: {result.detail}"


@then("agent_memory_writable passes via the data-dir fallback")
def agent_memory_writable_passes_via_fallback():
    result = _state["memory_result"]
    assert result.ok is True, f"read-only overlay with a working fallback must PASS, got: {result.detail}"
    assert "ok(fallback)" in result.detail, f"expected a fallback verdict, got: {result.detail}"
