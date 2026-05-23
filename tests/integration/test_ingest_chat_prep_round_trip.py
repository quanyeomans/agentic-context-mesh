"""F30 outcome test — ``kairix ingest-chat`` + ``kairix prep`` round-trip.

Plan B-parity post-mortem (2026-05-21) RCA showed that 5233 green unit
+ contract + BDD tests masked a 5% production-path LoCoMo regression
(below the 11% pre-Plan-B baseline). Every layer's fakes hid the
composition's failure modes; no test exercised the subprocess chain
``kairix ingest-chat <transcript>`` → ``kairix prep <question>``
against a real ingested fact.

This is that test. It is the first concrete F30 baseline shrink and
the canonical reference shape for the remaining 37 surfaces.

Boundary chain exercised:

  subprocess([kairix, ingest-chat, <transcript>])
    → kairix/use_cases/ingest_chat.py
    → LLMFactExtractor (real LLM, KV-resolved creds)
    → SQLiteFactStore.add

  subprocess([kairix, prep, <question>])
    → kairix/agents/prep/cli.py
    → run_prep
    → build_search_pipeline (auto-wires fact_retriever because facts table exists)
    → intent classifier → ATTRIBUTE_FACT
    → SearchPipeline.search → fact federation → fusion
    → _format_context (D1 — fact rows survive the chunk floor)
    → LLM synthesis → stdout

Sabotage-proof: reverting D1 (``is_fact`` exemption in ``_format_context``)
drops the fact snippet, the synthesiser sees empty context, ``run_prep``
returns the early-return "No relevant documents" — and this test fails
on the "VP / Vice President in stdout" assertion. Tested locally
2026-05-21 against develop HEAD 68be989a with the D1+D2+D4 fixes in
place; restoring the sabotage on a side branch reproduces the LoCoMo
regression as a single failing assertion in <30 seconds.

Run requirements (all three must be true; the test skips cleanly when
any are absent):

  * ``KAIRIX_E2E=1`` in the process env (per ``tests/conftest.py`` —
    only e2e-marked tests are allowed to hit real Azure credentials).
  * ``KAIRIX_KV_NAME`` resolvable to a Key Vault containing the
    ``kairix-llm-*`` and ``kairix-embed-*`` secrets.
  * ``az`` CLI on ``PATH`` AND the operator logged in
    (``az account show`` returns 0).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Skip gates
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
# Test fixture — minimal ingest payload + scratch env
# ---------------------------------------------------------------------------


_TRANSCRIPT_TURNS = [
    {
        "role": "user",
        "speaker": "ops",
        "content": "Quick reminder: agent-alpha is the VP of People at Acme. She owns onboarding.",
    },
    {
        "role": "assistant",
        "speaker": "agent",
        "content": "Got it — agent-alpha (VP of People, Acme) is the onboarding owner.",
    },
]


def _write_transcript(tmp_path: Path) -> Path:
    """Write a 2-turn JSONL transcript naming agent-alpha as VP of People."""
    path = tmp_path / "session-001.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for turn in _TRANSCRIPT_TURNS:
            f.write(json.dumps(turn) + "\n")
    return path


def _write_kairix_config(tmp_path: Path) -> Path:
    """Write the minimum ``kairix.config.yaml`` needed for the subprocess.

    Just the ``provider:`` field — credential resolution then flows
    through ``KAIRIX_KV_NAME`` per the Plan B-parity operations
    runbook (``docs/operations/runbooks/how-to-upgrade-kairix.md``).
    """
    cfg = tmp_path / "kairix.config.yaml"
    cfg.write_text("provider: azure_foundry\n", encoding="utf-8")
    return cfg


def _kairix_subprocess_env(tmp_path: Path, cfg: Path) -> dict[str, str]:
    """Build the env dict for kairix subprocesses.

    Carries the operator's ``KAIRIX_KV_NAME`` + ``PATH`` + Azure CLI
    creds; pins data/config/document paths to ``tmp_path`` so the test
    can't see (or write to) the operator's real knowledge store.
    """
    env = {
        # Inherit shell PATH so ``az``, ``kairix``, etc. resolve.
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        # Azure CLI auth cache (``az`` reads token from ~/.azure).
        "AZURE_CONFIG_DIR": os.environ.get("AZURE_CONFIG_DIR", str(Path.home() / ".azure")),
        # KV name carries through to ``kairix.secrets._read_secret_from_keyvault``.
        "KAIRIX_KV_NAME": os.environ["KAIRIX_KV_NAME"],
        # Pin everything to ``tmp_path`` — no operator state leakage.
        # ``KAIRIX_DB_PATH`` is pinned explicitly because kairix's default
        # ``cache_dir`` resolves to ``~/.cache/kairix/`` on macOS / Linux —
        # outside ``tmp_path``. Without this, the new evidence_at probe
        # test (which reads the SQLite directly) walks the wrong directory
        # and fails its "no sqlite found" assertion.
        "KAIRIX_CONFIG_PATH": str(cfg),
        "KAIRIX_DATA_DIR": str(tmp_path / "data"),
        "KAIRIX_DOCUMENT_ROOT": str(tmp_path / "docs"),
        "KAIRIX_WORKSPACE_ROOT": str(tmp_path / "workspace"),
        "KAIRIX_DB_PATH": str(tmp_path / "data" / "index.sqlite"),
        # Trace on — D4's diagnostic log line proves fact survival.
        "KAIRIX_TRACE": "1",
        # E2E flag — surfaces in the subprocess for any downstream gate.
        "KAIRIX_E2E": "1",
    }
    return env


# ---------------------------------------------------------------------------
# Outcome test
# ---------------------------------------------------------------------------


def test_ingest_chat_then_prep_surfaces_agent_alpha_fact(tmp_path: Path) -> None:
    """End-to-end: ingest a transcript naming agent-alpha's role; ``kairix prep``
    surfaces it.

    This is F30's reference outcome shape — subprocess + realistic input +
    assertion on stdout content. The assertion is robust to LLM phrasing
    (matches either ``VP`` or ``Vice President`` case-insensitively) but
    pinned to the anti-template: "No relevant content found in the
    knowledge store" must NOT appear, because that's the failure mode
    the Plan B-parity LoCoMo regression presented.
    """
    skip_reason = _missing_prereqs()
    if skip_reason is not None:
        pytest.skip(reason=skip_reason)

    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "workspace").mkdir()
    transcript = _write_transcript(tmp_path)
    cfg = _write_kairix_config(tmp_path)
    env = _kairix_subprocess_env(tmp_path, cfg)

    # Step 1 — ingest the transcript via the production CLI subprocess.
    ingest = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "ingest-chat", str(transcript)],
        capture_output=True,
        timeout=180,
        check=False,
        env=env,
    )
    ingest_stdout = ingest.stdout.decode("utf-8", errors="replace")
    ingest_stderr = ingest.stderr.decode("utf-8", errors="replace")
    assert ingest.returncode == 0, (
        f"ingest-chat exited {ingest.returncode}\nstdout:\n{ingest_stdout}\nstderr:\n{ingest_stderr}"
    )

    # Step 2 — query for the fact via the production prep subprocess.
    prep = subprocess.run(
        [sys.executable, "-m", "kairix.cli", "prep", "What is agent-alpha's role?"],
        capture_output=True,
        timeout=120,
        check=False,
        env=env,
    )
    prep_stdout = prep.stdout.decode("utf-8", errors="replace")
    prep_stderr = prep.stderr.decode("utf-8", errors="replace")
    assert prep.returncode == 0, f"prep exited {prep.returncode}\nstdout:\n{prep_stdout}\nstderr:\n{prep_stderr}"

    # Anti-template — the LoCoMo regression's signature failure mode.
    assert "No relevant content found" not in prep_stdout, (
        "prep emitted the anti-template — the fact was retrieved but dropped before "
        "synthesis. Likely re-emergence of D1 (chunk-floor filtering fact rows). "
        f"Full prep stdout:\n{prep_stdout}\nstderr:\n{prep_stderr}"
    )
    assert "No relevant documents found" not in prep_stdout, (
        "prep emitted the early-return — fact_retriever returned no hits. Either "
        "ingest-chat extracted no facts (Cap #2 prompt drift) or fact federation "
        "failed to fire (Cap #5 wiring regression). "
        f"Full prep stdout:\n{prep_stdout}\nstderr:\n{prep_stderr}"
    )

    # Robust value assertion — LLM may phrase "VP" or "Vice President".
    out_lower = prep_stdout.lower()
    matched = "vp" in out_lower or "vice president" in out_lower
    assert matched, (
        "prep response should mention 'VP' or 'Vice President' (agent-alpha's role "
        "in the ingested transcript). Got:\n"
        f"{prep_stdout}\n"
        f"stderr:\n{prep_stderr}"
    )

    # Sources should reference agent-alpha (case-insensitive — LLM may title-case).
    assert "agent-alpha" in out_lower, (
        f"prep response should reference agent-alpha. Got:\n{prep_stdout}\nstderr:\n{prep_stderr}"
    )


# ---------------------------------------------------------------------------
# Stream A Lever A — F30 outcome for session-date injection
# ---------------------------------------------------------------------------


def test_ingest_chat_with_session_date_persists_evidence_at(tmp_path: Path) -> None:
    """End-to-end: ingest-chat with --metadata pins evidence_at on facts.

    Stream A Lever A F30 surface: ``kairix ingest-chat --metadata`` →
    LLM extractor sees session ``date_time`` → emitted FactRecords carry
    ``evidence_at`` → SQLite persists it → reading the row back surfaces
    the date.

    The assertion reads the persisted SQLite directly (rather than via
    ``kairix prep``) so we pin the field-level wiring, not the
    retrieval+synthesis path. The earlier round-trip test pins the
    full retrieval chain.

    Sabotage-proof: drop the ``session_metadata=resolved_metadata``
    kwarg from the ``fact_extractor.extract`` call in ``ingest_chat``
    and this test fails because the persisted ``evidence_at`` is
    ``None`` instead of the session date.

    Run gates match the earlier round-trip test (KAIRIX_E2E=1,
    KAIRIX_KV_NAME, az CLI). Skips cleanly when any are absent.
    """
    skip_reason = _missing_prereqs()
    if skip_reason is not None:
        pytest.skip(reason=skip_reason)

    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "workspace").mkdir()
    transcript = _write_transcript(tmp_path)
    cfg = _write_kairix_config(tmp_path)
    env = _kairix_subprocess_env(tmp_path, cfg)

    # Stream A Lever A — write a --metadata sidecar carrying the
    # session date_time the extractor should pin onto every emitted
    # fact's ``evidence_at`` field.
    metadata_path = tmp_path / "session.metadata.json"
    metadata_path.write_text(
        json.dumps({"date_time": "2026-05-04 14:30", "session_id": "s-12"}),
        encoding="utf-8",
    )

    ingest = subprocess.run(
        [
            sys.executable,
            "-m",
            "kairix.cli",
            "ingest-chat",
            str(transcript),
            "--metadata",
            str(metadata_path),
        ],
        capture_output=True,
        timeout=180,
        check=False,
        env=env,
    )
    assert ingest.returncode == 0, (
        f"ingest-chat exited {ingest.returncode}\nstdout:\n{ingest.stdout!r}\nstderr:\n{ingest.stderr!r}"
    )

    # Read the SQLite directly so this assertion pins the storage
    # contract independently of retrieval-side wiring.
    import sqlite3

    db_path = tmp_path / "data" / "kairix.db"
    # The CLI may also create a different filename via KAIRIX_DB_PATH;
    # probe both locations.
    if not db_path.exists():
        alt = list((tmp_path / "data").glob("*.sqlite*")) + list((tmp_path / "data").glob("*.db"))
        assert alt, f"no sqlite found in {tmp_path / 'data'!s}; CLI may have failed silently"
        db_path = alt[0]

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT entity, attribute, evidence_at FROM facts WHERE superseded_by IS NULL").fetchall()
    finally:
        conn.close()

    assert rows, "ingest-chat produced no facts — Cap #2 extractor may have drifted"
    # At least one extracted fact carries the session-date anchor.
    detail_rows = "\n".join(f"  {r['entity']} / {r['attribute']} / {r['evidence_at']}" for r in rows)
    assert any(r["evidence_at"] == "2026-05-04 14:30" for r in rows), (
        f"no fact pinned evidence_at to session date. Rows:\n{detail_rows}"
    )
