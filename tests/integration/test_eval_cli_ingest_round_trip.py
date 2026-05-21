"""F30 outcome test — ``kairix eval`` extracts and persists facts end-to-end.

Closes the verification gap that motivated the
:mod:`kairix.corpus.wiring` composition root: before the wiring landed,
``kairix eval`` defaulted to ``_NullFactExtractor`` (returns ``[]``
regardless of input). The CLI passed every existing unit test while
extracting 0/N facts on real conversational corpora — the Plan B-parity
post-mortem's 5% LoCoMo collapse.

The shape exercises the canonical F30 outcome assertion: spawn the
real ``kairix eval`` subprocess against a tiny single-fact corpus, then
crack open the resulting SQLite database and assert the ``facts`` table
has at least one row. The assertion is on production state (a SQLite
row count), not on subprocess returncode or internal fake call-counts.

Boundary chain exercised:

  subprocess([kairix, eval, <suite>, --legacy-direct])
    → kairix/use_cases/eval_suite.py:main
    → _resolve_deps (no fact_extractor override)
    → _resolve_production_fact_extractor
    → kairix.corpus.wiring.make_production_fact_extractor
    → LLMFactExtractor (real LLM, KV-resolved creds)
    → SuiteRunner._ingest_sessions → fact_store.add → SQLite

  test reads <data-dir>/kairix.db facts table → asserts rows >= 1

Run requirements (all must be true; the test skips cleanly when any
are absent):

  * ``KAIRIX_E2E=1`` in the process env.
  * ``KAIRIX_KV_NAME`` resolvable to a Key Vault.
  * ``az`` CLI on PATH AND the operator logged in.

Sabotage proof (executed locally 2026-05-21 with KAIRIX_E2E=1 +
KAIRIX_KV_NAME=kv-tc-agents):

  Step 1 — confirm green on develop tip with the wiring in place:
    pytest tests/integration/test_eval_cli_ingest_round_trip.py::test_eval_cli_ingests_facts_via_production_wiring \\
        → PASS, facts_row_count >= 1.

  Step 2 — sabotage: revert ``_resolve_deps`` in eval_suite.py back to
    ``resolved_extractor = fact_extractor if fact_extractor is not None
    else _NullFactExtractor()`` (the pre-wiring behaviour).

  Step 3 — re-run with creds:
    pytest tests/integration/test_eval_cli_ingest_round_trip.py::test_eval_cli_ingests_facts_via_production_wiring \\
        → FAIL: ``facts table empty — production wiring regressed``.

  Step 4 — restore the production-wiring branch; test goes green again.

The sabotage cycle confirms the assertion is bound to the production
wiring, not to any side-effect of the SQLite layer (which would
populate facts regardless of the extractor's path).
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Skip gates — duplicated locally so the file has no cross-test imports
# (matches the convention in test_eval_via_prep_round_trip.py).
# ---------------------------------------------------------------------------


def _e2e_enabled() -> bool:
    """``KAIRIX_E2E=1`` opts into real-credential e2e."""
    return os.environ.get("KAIRIX_E2E") == "1"


def _kv_name() -> str | None:
    """Return ``KAIRIX_KV_NAME`` when set, else None."""
    name = os.environ.get("KAIRIX_KV_NAME")
    return name or None


def _az_login_active() -> bool:
    """True when ``az account show`` returns 0 — operator is logged in."""
    if shutil.which("az") is None:
        return False
    try:
        proc = subprocess.run(
            ["az", "account", "show"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return proc.returncode == 0


def _missing_prereqs() -> str | None:
    """Return a human-readable skip reason, or None when everything is wired."""
    if not _e2e_enabled():
        return "KAIRIX_E2E=1 required (tests/conftest.py blocks real Azure calls by default)"
    if _kv_name() is None:
        return "KAIRIX_KV_NAME required — point at the Key Vault holding kairix-llm-* secrets"
    if not _az_login_active():
        return "az CLI not on PATH or operator not logged in (run `az login`)"
    return None


# ---------------------------------------------------------------------------
# Fixture data — a single-fact corpus rigged to exercise extraction.
# ---------------------------------------------------------------------------


_TRANSCRIPT_TURNS = [
    {
        "role": "user",
        "speaker": "agent-alpha",
        "content": "Quick reminder: Bob is the CTO of Acme. He owns engineering.",
    },
    {
        "role": "assistant",
        "speaker": "agent-beta",
        "content": "Got it — Bob (CTO, Acme) is the engineering owner.",
    },
]


def _write_eval_suite(suite_dir: Path) -> None:
    """Lay out a single-question eval suite with one ingestable session.

    The suite-runner ingests ``session-001.jsonl`` through the production
    fact-extractor (now LLMFactExtractor by default) and stores emitted
    records in the same SQLite database the runner reads back. The
    ground-truth-queries.json is required by the suite-runner contract
    but the assertion in this test does NOT depend on a particular
    judge score — only on the presence of rows in the facts table.
    """
    suite_dir.mkdir(parents=True, exist_ok=True)
    transcript = suite_dir / "session-001.jsonl"
    with transcript.open("w", encoding="utf-8") as fh:
        for turn in _TRANSCRIPT_TURNS:
            fh.write(json.dumps(turn) + "\n")
    (suite_dir / "ground-truth-queries.json").write_text(
        json.dumps(
            [
                {
                    "question": "What is Bob's role?",
                    "answer": "CTO",
                    "category": "single-hop",
                }
            ]
        ),
        encoding="utf-8",
    )


def _write_kairix_config(tmp_path: Path) -> Path:
    """Write the minimum ``kairix.config.yaml`` needed for the subprocess."""
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text("provider: azure_foundry\n", encoding="utf-8")
    return cfg


def _kairix_subprocess_env(tmp_path: Path, cfg: Path) -> dict[str, str]:
    """Build the env dict for the kairix subprocess.

    Carries operator's ``KAIRIX_KV_NAME`` + ``PATH`` + Azure CLI creds;
    pins data/config/document paths to ``tmp_path`` so the test can't
    see (or write to) the operator's real knowledge store.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "AZURE_CONFIG_DIR": os.environ.get("AZURE_CONFIG_DIR", str(Path.home() / ".azure")),
        "KAIRIX_KV_NAME": os.environ["KAIRIX_KV_NAME"],
        "KAIRIX_CONFIG_PATH": str(cfg),
        "KAIRIX_DATA_DIR": str(tmp_path / "data"),
        "KAIRIX_DOCUMENT_ROOT": str(tmp_path / "docs"),
        "KAIRIX_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "KAIRIX_E2E": "1",
    }
    return env


def _count_facts_rows(db_path: Path) -> int:
    """Return the row count of the ``facts`` table (0 if table is absent).

    The Suite-Runner populates this table via ``SQLiteFactStore.add``
    when the production fact-extractor returns records. Absence-of-table
    is the legitimate "no ingest happened" signal — the assertion below
    treats both 0-rows and missing-table as the regression failure mode.
    """
    if not db_path.exists():
        return 0
    with sqlite3.connect(str(db_path)) as conn:
        has_table = bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='facts' LIMIT 1").fetchone()
        )
        if not has_table:
            return 0
        row = conn.execute("SELECT COUNT(*) FROM facts").fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# F30 outcome test
# ---------------------------------------------------------------------------


def test_eval_cli_ingests_facts_via_production_wiring(tmp_path: Path) -> None:
    """``kairix eval`` on a conversational suite populates the facts table.

    This is the canonical F30 outcome shape for the production-wiring
    fix: subprocess + realistic input + assertion on database state
    (NOT on returncode alone, NOT on internal fake call-counts).

    Sabotage-proof: revert ``_resolve_deps`` in
    ``kairix/use_cases/eval_suite.py`` to ``resolved_extractor =
    fact_extractor if fact_extractor is not None else
    _NullFactExtractor()`` (the pre-wiring behaviour). Re-run with
    ``KAIRIX_E2E=1 KAIRIX_KV_NAME=kv-tc-agents`` and the
    ``facts_row_count >= 1`` assertion fails because the Null extractor
    emits zero facts (the LoCoMo 5% collapse). Restore to pass.
    """
    skip_reason = _missing_prereqs()
    if skip_reason is not None:
        pytest.skip(reason=skip_reason)

    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "workspace").mkdir()

    suite_dir = tmp_path / "eval-suite"
    _write_eval_suite(suite_dir)
    cfg = _write_kairix_config(tmp_path)
    env = _kairix_subprocess_env(tmp_path, cfg)

    # Run the eval CLI in legacy-direct mode so we exercise the
    # ingest path the suite runner uses, without also pulling in the
    # full SearchPipeline. Coverage of the via-prep path lives in
    # tests/integration/test_eval_via_prep_round_trip.py.
    proc = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "eval", str(suite_dir), "--json", "--legacy-direct"],
        capture_output=True,
        timeout=240,
        check=False,
        env=env,
    )
    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    # Pin the F30 contract on observable subprocess + DB state.
    assert proc.returncode == 0, f"eval exited {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"

    # Sanity: --json output parses (proves the CLI completed the suite
    # runner round-trip, not just argparse).
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"eval --json output was not valid JSON: {exc}\nstdout:\n{stdout}\nstderr:\n{stderr}")
    assert payload["suite_name"] == "eval-suite"
    assert payload["n_questions"] == 1

    # The canonical assertion — the facts table must have at least one
    # row. ZERO rows means the production wiring regressed to the
    # _NullFactExtractor default (the LoCoMo 5% failure mode).
    db_path = tmp_path / "data" / "kairix.db"
    facts_row_count = _count_facts_rows(db_path)
    assert facts_row_count >= 1, (
        f"facts table has {facts_row_count} rows after kairix eval — "
        f"production wiring regression. "
        f"fix: check _resolve_deps in kairix/use_cases/eval_suite.py wires "
        f"make_production_fact_extractor (not _NullFactExtractor). "
        f"next: re-run pytest tests/integration/test_eval_cli_ingest_round_trip.py "
        f"after restoring the wiring. "
        f"run: grep -n make_production_fact_extractor kairix/use_cases/eval_suite.py.\n"
        f"db_path={db_path}\n"
        f"eval stdout:\n{stdout}\n"
        f"eval stderr:\n{stderr}"
    )
